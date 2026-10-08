"""Persistent real inputs and immutable qualification runs."""
import csv
import hashlib
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager

from .engine import ROUTES, validate_profile, text
from .research import clean_url, timestamp


def ident():
    return uuid.uuid4().hex[:20]


def validate_input(row):
    if not isinstance(row, dict):
        raise ValueError("Lead must be an object.")
    clean = {"company":text(row.get("company"), "Company", 120),
             "website":clean_url(row.get("website", "")),
             "inquiry":text(row.get("inquiry", ""), "Inquiry", 12000, required=False)}
    if not clean["website"] and not clean["inquiry"]:
        raise ValueError("Supply a website or inquiry for each company.")
    clean["fingerprint"] = hashlib.sha256(json.dumps({k:" ".join(v.lower().split()) for k,v in clean.items()}, sort_keys=True).encode()).hexdigest()
    return clean


def parse_csv(content):
    if not isinstance(content, str) or len(content.encode()) > 1500000:
        raise ValueError("Upload a UTF-8 CSV smaller than 1.5 MB.")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("The CSV is empty.")
    headers = [name.strip().lower() for name in reader.fieldnames]
    if len(headers) != len(set(headers)) or "company" not in headers or not ({"website", "inquiry"} & set(headers)):
        raise ValueError("Use unique column names including company, plus website and/or inquiry. Download the template for an example.")
    reader.fieldnames = headers
    rows = []
    for line, row in enumerate(reader, 2):
        if len(rows) >= 100:
            raise ValueError("Import at most 100 leads per file.")
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV row {} has the wrong number of columns.".format(line))
        if not any(value.strip() for value in row.values()):
            continue
        try:
            rows.append(validate_input({k:row.get(k, "") for k in ("company", "website", "inquiry")}))
        except ValueError as error:
            raise ValueError("CSV row {}: {}".format(line, error))
    if not rows:
        raise ValueError("The CSV contains no lead rows.")
    return rows


class Workspace:
    def __init__(self, path):
        from pathlib import Path
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, lead_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, route TEXT NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT body FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["body"]) if row else default

    def set_setting(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def save_profile(self, profile):
        clean = validate_profile(profile)
        self.set_setting("profile", clean)
        return clean

    def import_rows(self, rows):
        validated = [validate_input(row) for row in rows]
        added, skipped = [], 0
        with self.db() as db:
            for row in validated:
                row.update(id=ident(), created_at=timestamp())
                cursor = db.execute("INSERT OR IGNORE INTO contacts VALUES (?,?,?,?)",
                                    (row["id"],row["fingerprint"],json.dumps(row),row["created_at"]))
                if cursor.rowcount:
                    added.append(row["id"])
                else:
                    skipped += 1
        return {"added":added, "skipped":skipped}

    def lead(self, lead_id):
        with self.db() as db:
            row = db.execute("SELECT body FROM contacts WHERE id=?", (lead_id,)).fetchone()
        if not row:
            raise KeyError("Lead not found.")
        return json.loads(row["body"])

    def leads(self):
        with self.db() as db:
            leads = [json.loads(r["body"]) for r in db.execute("SELECT body FROM contacts ORDER BY rowid DESC")]
            runs = [json.loads(r["body"]) for r in db.execute("SELECT body FROM runs ORDER BY rowid DESC")]
            reviews = [dict(r) for r in db.execute("SELECT * FROM decisions ORDER BY id DESC")]
        profile = self.setting("profile")
        latest = {}
        for run in runs:
            latest.setdefault(run["lead_id"], run)
        for lead in leads:
            run = latest.get(lead["id"])
            lead["latest_run"] = run
            lead["run_count"] = sum(r["lead_id"] == lead["id"] for r in runs)
            lead["review"] = next((r for r in reviews if run and r["run_id"] == run["id"]), None)
            lead["route"] = lead["review"]["route"] if lead["review"] else run["result"]["route"] if run else None
            lead["stale"] = bool(run and profile and run["profile"]["version"] != profile["version"])
        return leads

    def details(self, lead_id):
        lead = next((l for l in self.leads() if l["id"] == lead_id), None)
        if not lead:
            raise KeyError("Lead not found.")
        with self.db() as db:
            lead["runs"] = [json.loads(r["body"]) for r in db.execute("SELECT body FROM runs WHERE lead_id=? ORDER BY rowid DESC", (lead_id,))]
            for run in lead["runs"]:
                run["reviews"] = [dict(r) for r in db.execute("SELECT * FROM decisions WHERE run_id=? ORDER BY id DESC", (run["id"],))]
        return lead

    def save_run(self, lead_id, run):
        self.lead(lead_id)
        run = dict(run, id=ident(), lead_id=lead_id, created_at=timestamp())
        with self.db() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run["id"], lead_id, json.dumps(run), run["created_at"]))
        return run

    def review(self, run_id, route, reason):
        if route not in ROUTES:
            raise ValueError("Choose a valid route.")
        reason = text(reason, "Review reason", 1500, required=False)
        with self.db() as db:
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("Qualification run not found.")
            run = json.loads(row["body"])
            if route != run["result"]["route"] and not reason:
                raise ValueError("Explain why you are changing the recommended route.")
            db.execute("INSERT INTO decisions(run_id,route,reason,created_at) VALUES (?,?,?,?)",
                       (run_id,route,reason or "Accepted the recommendation.",timestamp()))
        return self.details(run["lead_id"])

    def save_job(self, job):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?)", (job["id"],json.dumps(job)))

    def jobs(self):
        with self.db() as db:
            return [json.loads(r["body"]) for r in db.execute("SELECT body FROM jobs ORDER BY rowid DESC LIMIT 50")]


def csv_safe(value):
    text_value = str(value if value is not None else "")
    return "'" + text_value if text_value.lstrip().startswith(("=", "+", "-", "@")) else text_value


def export_csv(leads):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["company","website","inquiry","recommended_route","current_route","owner","fit_low","fit_high",
                     "intent_low","intent_high","readiness_low","readiness_high","coverage_percent","profile_version",
                     "analysis_method","model","estimated_cost_usd","source_urls","review_reason","needs_requalification"])
    for lead in leads:
        run, review = lead.get("latest_run"), lead.get("review")
        result = run["result"] if run else {}
        dims, usage = result.get("dimensions", {}), run.get("usage", {}) if run else {}
        scores = [dims.get(g, {}).get(bound, "") for g in ("fit","intent","readiness") for bound in ("low","high")]
        fields = [lead["company"],lead["website"],lead["inquiry"],result.get("route"),lead.get("route"),
                  run["profile"]["owners"].get(lead["route"], "") if run else ""] + scores + [
                  result.get("coverage"),run["profile"]["version"] if run else "",run.get("method") if run else "",
                  usage.get("model"),usage.get("estimated_cost_usd"),
                  " | ".join(s["url"] for s in run.get("sources", []) if s.get("url")) if run else "",
                  review["reason"] if review else "",lead.get("stale",False)]
        writer.writerow([csv_safe(value) for value in fields])
    return output.getvalue()


def brief(lead):
    run = lead.get("latest_run")
    lines = ["# " + lead["company"], "", "Website: " + lead["website"], "", "## Inquiry", "", lead["inquiry"], ""]
    if not run:
        return "\n".join(lines + ["Not yet qualified."])
    result, profile = run["result"], run["profile"]
    lines += ["## Qualification", "", "- Recommended route: " + result["route"], "- Current route: " + lead["route"],
              "- Owner: " + profile["owners"][lead["route"]], "- Method: " + run["method"],
              "- Product: " + profile["name"] + " (profile " + profile["version"] + ")", "- Run: " + run["created_at"], "", result["reason"], ""]
    if lead.get("stale"):
        lines += ["The product profile changed after this run. Requalification is needed to apply the current criteria.", ""]
    criteria = {c["id"]:c for c in profile["criteria"]}
    for a in run["assessments"]:
        lines += ["### " + criteria[a["criterion_id"]]["label"] + " — " + a["status"], "", a["reason"]]
        for citation in a["citations"]:
            lines += ["", "> " + citation["quote"].replace("\n", "\n> "), "", "Source: " + citation["source_id"]]
        lines.append("")
    lines += ["## Source snapshots", ""]
    for source in run["sources"]:
        lines += ["### " + source["id"] + " — " + source["title"], "", source.get("url", ""),
                  "Retrieved/supplied: " + source["retrieved_at"], "", source["text"], ""]
    lines += ["## Questions", ""] + ["- " + q for q in result["questions"]]
    lines += ["", "## Warnings", ""] + (["- " + w for w in run["warnings"]] or ["None."])
    lines += ["", "## Review history", ""]
    for historic in lead.get("runs", []):
        for review in historic.get("reviews", []):
            lines.append("- {} / run {}: {} — {}".format(review["created_at"],historic["id"],review["route"],review["reason"]))
    lines += ["", "Scores are qualification heuristics, not calibrated purchase probabilities. Quotes establish provenance; the reviewer must assess whether they justify the conclusions."]
    return "\n".join(lines) + "\n"
