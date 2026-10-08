"""Local-only server. Run with python3 -m app.server. No packages required."""
import argparse
import csv
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .qualification import FIELDS, ROUTES, OWNERS, qualify, validate_lead
from .evaluate import evaluate

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS leads (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, lead_id TEXT NOT NULL,
                    route TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    recommendation TEXT NOT NULL, rubric_version TEXT NOT NULL);
            """)
            for lead in json.loads((ROOT / "data" / "leads.json").read_text()):
                validate_lead(lead)
                db.execute("INSERT OR IGNORE INTO leads VALUES (?, ?)", (lead["id"], json.dumps(lead)))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connect() as db:
            leads = [json.loads(row["body"]) for row in db.execute("SELECT body FROM leads ORDER BY rowid")]
            reviews = [dict(row) for row in db.execute("SELECT * FROM reviews ORDER BY seq DESC")]
        leads = ([lead for lead in reversed(leads) if lead.get("kind") == "manual"]
                 + [lead for lead in leads if lead.get("kind") != "manual"])
        for lead in leads:
            lead["qualification"] = qualify(lead)
            lead["reviews"] = [r for r in reviews if r["lead_id"] == lead["id"]]
            lead["review"] = lead["reviews"][0] if lead["reviews"] else None
            lead["effective_route"] = lead["review"]["route"] if lead["review"] else lead["qualification"]["route"]
        return leads

    def get(self, ident):
        return next((lead for lead in self.list() if lead["id"] == ident), None)

    def add(self, payload):
        validate_lead(payload)
        normalize = lambda text: " ".join(text.casefold().split())
        for existing in self.list():
            if (normalize(existing["company"]) == normalize(payload["company"])
                    and normalize(existing["inquiry"]) == normalize(payload["inquiry"])):
                raise ValueError("This company and inquiry already exist in the queue. Review the existing lead instead.")
        # Incoming records contain only manually supplied facts; no extractor is implied.
        lead = {key: payload[key] for key in ("company", "inquiry")}
        lead.update(sources=payload.get("sources", []), facts=payload.get("facts", {}))
        lead.update(id=uuid.uuid4().hex[:16], kind="manual", sector="Manual intake", tag="Your scenario",
                    website="", created_at=now(), conflict=payload.get("conflict", False),
                    conflict_note=payload.get("conflict_note", ""))
        with self.connect() as db:
            db.execute("INSERT INTO leads VALUES (?, ?)", (lead["id"], json.dumps(lead)))
        return self.get(lead["id"])

    def review(self, ident, payload):
        lead = self.get(ident)
        if lead is None:
            raise KeyError(ident)
        route, reason = payload.get("route"), payload.get("reason", "")
        if route not in ROUTES or not isinstance(reason, str) or len(reason) > 2000:
            raise ValueError("Choose a valid route and keep the reason under 2,000 characters.")
        if route != lead["qualification"]["route"] and not reason.strip():
            raise ValueError("A reason is required when overriding the recommendation.")
        q = lead["qualification"]
        with self.connect() as db:
            db.execute("INSERT INTO reviews (lead_id,route,reason,created_at,recommendation,rubric_version) VALUES (?,?,?,?,?,?)",
                       (ident, route, reason.strip() or "Accepted the recommendation.", now(), q["route"], q["rubric_version"]))
        return self.get(ident)


def markdown(lead):
    q = lead["qualification"]
    lines = ["# {} — qualification brief".format(lead["company"]), "",
             "Offline demonstration. {}. No website research or AI calls were performed.".format(
                 "Fictional company and inquiry" if lead["kind"] == "fictional" else "Manually supplied scenario and facts"), "",
             "- Recommended route: " + q["route"], "- Current route: " + lead["effective_route"],
             "- Current owner: " + OWNERS[lead["effective_route"]], "- Rule: " + q["rule"],
             "- Rubric version: " + q["rubric_version"], "", "## Rationale", "", q["reason"], "",
             "## Inquiry", "", lead["inquiry"], "", "## Assessment", ""]
    for dimension in q["dimensions"]:
        lines.append("- {}: {}–{}/100; {}% of weighted evidence known".format(dimension["name"], dimension["low"], dimension["high"], dimension["coverage"]))
    lines += ["", "Scores are heuristic ranges, not probabilities. Unknown facts can widen the range.", "", "## Evidence", ""]
    for key, label in FIELDS.items():
        fact = lead["facts"].get(key, {})
        value = fact.get("value")
        lines.append("- {}: {} [source: {}]".format(label, "Unknown" if value is None else value, fact.get("source_id") or "none"))
    for source in lead["sources"]:
        lines += ["", "### {} ({})".format(source["title"], source["id"]), "", source["excerpt"]]
    if lead.get("conflict"):
        lines += ["", "## Unresolved conflict", "", lead["conflict_note"]]
    lines += ["", "## Discovery questions", ""] + ["- " + question for question in q["questions"]]
    lines += ["", "## Review history", ""]
    for review in lead["reviews"]:
        lines.append("- {}: {} — {}".format(review["created_at"], review["route"], review["reason"]))
    if not lead["reviews"]:
        lines.append("Not yet reviewed.")
    return "\n".join(lines) + "\n"


def spreadsheet_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_csv(leads):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Company", "Data type", "Recommended route", "Current route", "Owner", "Evidence coverage %", "Reviewed", "Review reason", "Rubric"])
    for lead in leads:
        q, review = lead["qualification"], lead["review"]
        writer.writerow([spreadsheet_safe(v) for v in (lead["company"], lead["kind"], q["route"], lead["effective_route"],
                         OWNERS[lead["effective_route"]], q["coverage"], bool(review), review["reason"] if review else "", q["rubric_version"])])
    return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def respond(self, code, body, content_type="application/json; charset=utf-8", filename=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(filename))
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        return host in ("localhost:" + str(port), "127.0.0.1:" + str(port))

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {"error": "Use the local demo address."})
        path = urlsplit(self.path).path
        if path == "/api/leads":
            return self.respond(200, {"leads": self.server.store.list(), "routes": ROUTES})
        if path == "/api/evaluation":
            return self.respond(200, evaluate())
        if path == "/api/export":
            return self.respond(200, export_csv(self.server.store.list()), "text/csv; charset=utf-8", "signaldesk-leads.csv")
        if path.startswith("/api/brief/"):
            lead = self.server.store.get(path.rsplit("/", 1)[-1])
            if not lead:
                return self.respond(404, {"error": "Lead not found."})
            return self.respond(200, markdown(lead), "text/markdown; charset=utf-8", lead["id"] + "-brief.md")
        mapping = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                   "/style.css": ("style.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
        if path in mapping:
            name, mime = mapping[path]
            return self.respond(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8")
        return self.respond(404, {"error": "Not found."})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self.valid_host() or (origin and origin != "http://" + self.headers.get("Host", "")):
            return self.respond(403, {"error": "Only requests from the local demo are accepted."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.respond(415, {"error": "Send JSON."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100000:
                return self.respond(413, {"error": "Request is empty or too large."})
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Send a JSON object.")
            path = urlsplit(self.path).path
            if path == "/api/leads":
                return self.respond(201, self.server.store.add(payload))
            if path.startswith("/api/review/"):
                return self.respond(200, self.server.store.review(path.rsplit("/", 1)[-1], payload))
            return self.respond(404, {"error": "Not found."})
        except KeyError:
            return self.respond(404, {"error": "Lead not found."})
        except (ValueError, TypeError) as error:
            return self.respond(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description="SignalDesk — local offline lead qualification demo")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--db", default=str(ROOT / ".data" / "signaldesk.db"))
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.store = Store(args.db)
    print("SignalDesk is running at http://127.0.0.1:{} — Ctrl+C to stop".format(args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
