"""Product-independent, evidence-grounded qualification policy."""
import hashlib
import json
import re

GROUPS = ("fit", "intent", "readiness")
ROUTES = ("Sales ready", "Discovery", "Nurture", "Not a fit", "Review needed")
OWNERS = dict(zip(ROUTES, ("Sales", "Solutions / sales", "Marketing", "Marketing", "Revenue operations")))
STATUSES = ("met", "not_met", "unknown", "conflict")
ENGINE_VERSION = "2.0"


def starter_profile():
    return {
        "name": "", "description": "", "customer": "",
        "thresholds": {"fit": 70, "intent": 60, "readiness": 60},
        "owners": dict(OWNERS),
        "criteria": [
            {"id": "fit1", "group": "fit", "label": "Relevant business", "description": "The company serves the target market described in the ideal customer profile.", "weight": 50, "required": True},
            {"id": "fit2", "group": "fit", "label": "Relevant use case", "description": "The stated problem is one the product can address.", "weight": 50, "required": False},
            {"id": "intent1", "group": "intent", "label": "Active evaluation", "description": "The prospect explicitly requests a demo, evaluation, or pilot of the product.", "weight": 70, "required": True},
            {"id": "intent2", "group": "intent", "label": "Defined timeline", "description": "The prospect explicitly states a desired start within 90 days.", "weight": 30, "required": False},
            {"id": "ready1", "group": "readiness", "label": "Project owner", "description": "The prospect explicitly identifies a person or team responsible for implementation.", "weight": 50, "required": True},
            {"id": "ready2", "group": "readiness", "label": "Prerequisites available", "description": "The prospect explicitly confirms access to the data, systems, or resources needed for this product.", "weight": 50, "required": False},
        ],
    }


def text(value, label, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError("{} must be {}text up to {} characters.".format(label, "non-empty " if required else "", maximum))
    return value.strip()


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("Supply a product profile.")
    clean = {k: text(profile.get(k), k.title(), n) for k, n in (("name", 100), ("description", 2500), ("customer", 2500))}
    criteria = profile.get("criteria")
    if not isinstance(criteria, list) or not 3 <= len(criteria) <= 12:
        raise ValueError("Use 3–12 criteria, including at least one for fit, intent, and readiness.")
    clean["criteria"] = []
    ids = set()
    for c in criteria:
        if not isinstance(c, dict):
            raise ValueError("Invalid criterion.")
        ident = text(c.get("id"), "Criterion ID", 40)
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", ident) or ident in ids:
            raise ValueError("Criterion IDs must be unique letters, numbers, underscores, or hyphens.")
        ids.add(ident)
        if c.get("group") not in GROUPS or type(c.get("weight")) is not int or not 1 <= c["weight"] <= 100:
            raise ValueError("Each criterion needs a valid group and a weight from 1 to 100.")
        if type(c.get("required")) is not bool:
            raise ValueError("Required criteria must be true or false.")
        clean["criteria"].append({"id": ident, "group": c["group"], "weight": c["weight"], "required": c["required"],
                                  "label": text(c.get("label"), "Criterion label", 100),
                                  "description": text(c.get("description"), "Criterion definition", 700)})
    if {c["group"] for c in clean["criteria"]} != set(GROUPS):
        raise ValueError("Include at least one criterion in each group.")
    thresholds = profile.get("thresholds", {})
    if not isinstance(thresholds, dict) or any(type(thresholds.get(g)) is not int or not 1 <= thresholds[g] <= 100 for g in GROUPS):
        raise ValueError("Each group threshold must be an integer from 1 to 100.")
    clean["thresholds"] = {g: thresholds[g] for g in GROUPS}
    owners = profile.get("owners", OWNERS)
    if not isinstance(owners, dict):
        raise ValueError("Invalid route owners.")
    clean["owners"] = {r: text(owners.get(r, OWNERS[r]), "Route owner", 100) for r in ROUTES}
    clean["version"] = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()[:12]
    return clean


def normalize(value):
    return " ".join(value.split())


def validate_assessments(profile, sources, supplied):
    """Reject unsupported citations; absence of text is never negative evidence."""
    if not isinstance(supplied, list):
        raise ValueError("Assessments must be a list.")
    by_id = {}
    for a in supplied:
        if not isinstance(a, dict) or a.get("criterion_id") in by_id:
            raise ValueError("Malformed or duplicate assessment.")
        by_id[a.get("criterion_id")] = a
    valid_ids = {c["id"] for c in profile["criteria"]}
    if set(by_id) != valid_ids:
        raise ValueError("The result must assess every configured criterion exactly once.")
    source_map = {s["id"]: s for s in sources}
    result, warnings = [], []
    for criterion in profile["criteria"]:
        a = by_id[criterion["id"]]
        status = a.get("status")
        if status not in STATUSES:
            raise ValueError("Invalid assessment status.")
        reason = text(a.get("reason"), "Assessment explanation", 1500)
        citations = a.get("citations")
        if not isinstance(citations, list) or len(citations) > 4:
            raise ValueError("Use at most four evidence citations per criterion.")
        checked = []
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            source = source_map.get(citation.get("source_id"))
            quote = citation.get("quote", "")
            if not source or not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 1500:
                continue
            if criterion["group"] != "fit" and source["kind"] != "inquiry":
                continue
            if normalize(quote) not in normalize(source["text"]):
                continue
            item = {"source_id": source["id"], "quote": quote.strip()}
            if item not in checked:
                checked.append(item)
        needs = 2 if status == "conflict" else 1
        if status != "unknown" and len(checked) < needs:
            warnings.append(criterion["label"] + ": unsupported assessment changed to unknown.")
            status, reason, checked = "unknown", "The supplied evidence did not support a valid citation. Human review is needed.", []
        result.append({"criterion_id": criterion["id"], "status": status, "reason": reason, "citations": checked})
    return result, warnings


def score(profile, assessments):
    by_id = {a["criterion_id"]: a for a in assessments}
    dimensions, exact = {}, {}
    for group in GROUPS:
        criteria = [c for c in profile["criteria"] if c["group"] == group]
        total = sum(c["weight"] for c in criteria)
        earned = sum(c["weight"] for c in criteria if by_id[c["id"]]["status"] == "met")
        unknown = sum(c["weight"] for c in criteria if by_id[c["id"]]["status"] in ("unknown", "conflict"))
        exact[group] = {"low": 100 * earned / total, "high": 100 * (earned + unknown) / total}
        dimensions[group] = {"low": round(100 * earned / total, 1), "high": round(100 * (earned + unknown) / total, 1),
                             "coverage": round(100 * (total - unknown) / total, 1)}
    def required(group, states):
        return any(c["required"] and c["group"] == group and by_id[c["id"]]["status"] in states for c in profile["criteria"])
    # Display rounding must never promote a lead across a policy threshold.
    fit, intent, ready = (exact[g] for g in GROUPS)
    limits = profile["thresholds"]
    if any(a["status"] == "conflict" for a in assessments):
        route, reason = "Review needed", "Conflicting evidence must be resolved before a handoff."
    elif required("fit", ("not_met",)) or fit["high"] < limits["fit"]:
        route, reason = "Not a fit", "A required fit criterion is not met, or even the upper fit score is below your threshold."
    elif required("fit", ("unknown",)) or fit["low"] < limits["fit"]:
        route, reason = "Review needed", "Available evidence does not yet establish the required customer fit."
    elif required("intent", ("not_met",)) or intent["high"] < limits["intent"]:
        route, reason = "Nurture", "Customer fit is established, but stated buying intent does not meet your qualification policy."
    elif required("intent", ("unknown",)) or intent["low"] < limits["intent"]:
        route, reason = "Discovery", "Customer fit is established; buying intent needs a conversation."
    elif required("readiness", ("not_met", "unknown")) or ready["low"] < limits["readiness"]:
        route, reason = "Discovery", "Fit and intent are established; implementation prerequisites need discovery."
    else:
        route, reason = "Sales ready", "Evidence meets the configured fit, intent, and readiness requirements."
    questions = ["Confirm {}: {}".format(c["label"].lower(), c["description"]) for c in profile["criteria"]
                 if by_id[c["id"]]["status"] in ("unknown", "conflict", "not_met")]
    return {"route": route, "owner": profile["owners"][route], "reason": reason, "dimensions": dimensions,
            "coverage": round(100 * sum(a["status"] in ("met", "not_met") for a in assessments) / len(assessments)),
            "questions": questions or ["Agree on the success criteria, purchasing process, and next step for an evaluation."],
            "engine_version": ENGINE_VERSION}
