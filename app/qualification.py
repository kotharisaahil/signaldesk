"""Deterministic qualification. Missing evidence stays unknown, never false."""

RUBRIC_VERSION = "1.0"
ROUTES = ("Enterprise sales", "Technical discovery", "Self-service", "Nurture", "Manual review")
FIELDS = {
    "support_workflow": "Customer-support use case",
    "monthly_tickets": "Monthly support tickets",
    "stack": "Help desk",
    "intent": "Stated buying intent",
    "timeline_days": "Target start, days",
    "kb_ready": "Knowledge base available",
    "implementation_owner": "Implementation owner assigned",
}
OWNERS = {
    "Enterprise sales": "Account executive",
    "Technical discovery": "Solutions engineer",
    "Self-service": "Product-led onboarding",
    "Nurture": "Lifecycle marketing",
    "Manual review": "Revenue operations",
}


def values(lead):
    return {key: fact.get("value") for key, fact in lead.get("facts", {}).items()}


def validate_lead(lead):
    if not isinstance(lead, dict):
        raise ValueError("Lead must be an object.")
    for field in ("company", "inquiry"):
        if not isinstance(lead.get(field), str) or not lead[field].strip():
            raise ValueError("Company and inquiry are required.")
    if len(lead["company"]) > 120 or len(lead["inquiry"]) > 8000:
        raise ValueError("Company or inquiry is too long.")
    sources = lead.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 20:
        raise ValueError("Provide up to 20 evidence sources.")
    ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Invalid source.")
        for key in ("id", "title", "excerpt"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                raise ValueError("Every source needs an ID, title, and excerpt.")
        if source["id"] in ids or len(source["excerpt"]) > 10000:
            raise ValueError("Duplicate source ID or oversized evidence.")
        ids.add(source["id"])
    facts = lead.get("facts", {})
    if not isinstance(facts, dict) or set(facts) - set(FIELDS):
        raise ValueError("Unknown qualification field.")
    for key, fact in facts.items():
        if not isinstance(fact, dict):
            raise ValueError("Invalid fact format.")
        val = fact.get("value")
        if val is None:
            continue
        if fact.get("source_id") not in ids:
            raise ValueError("Every known fact must reference an evidence source.")
        if key in ("support_workflow", "kb_ready", "implementation_owner") and type(val) is not bool:
            raise ValueError("Readiness and use-case fields must be yes, no, or unknown.")
        if key in ("monthly_tickets", "timeline_days") and (type(val) is not int or not 0 <= val <= 10000000):
            raise ValueError("Ticket counts and timelines must be non-negative integers.")
        if key == "stack" and val not in ("Zendesk", "Intercom", "Freshdesk", "Custom", "Other"):
            raise ValueError("Invalid help desk.")
        if key == "intent" and val not in ("Exploring", "Evaluating", "Pilot requested"):
            raise ValueError("Invalid buying intent.")
    if type(lead.get("conflict", False)) is not bool:
        raise ValueError("Conflict flag must be a boolean.")
    if not isinstance(lead.get("conflict_note", ""), str):
        raise ValueError("Conflict note must be text.")
    if lead.get("conflict") and not lead.get("conflict_note", "").strip():
        raise ValueError("Describe the conflicting evidence.")
    if len(lead.get("conflict_note", "")) > 2000:
        raise ValueError("Conflict note is too long.")


def qualify(lead):
    validate_lead(lead)
    v = values(lead)
    def item(key, weight, predicate):
        value = v.get(key)
        state = "unknown" if value is None else ("met" if predicate(value) else "not_met")
        return {"field": key, "label": FIELDS[key], "weight": weight, "state": state,
                "source_id": lead.get("facts", {}).get(key, {}).get("source_id")}

    groups = [
        ("Customer fit", [item("support_workflow", 50, bool),
                          item("monthly_tickets", 30, lambda x: x >= 1000),
                          item("stack", 20, lambda x: x in ("Zendesk", "Intercom"))]),
        ("Buying intent", [item("intent", 65, lambda x: x in ("Evaluating", "Pilot requested")),
                           item("timeline_days", 35, lambda x: x <= 90)]),
        ("Readiness", [item("kb_ready", 40, bool), item("implementation_owner", 35, bool),
                       item("stack", 25, lambda x: x in ("Zendesk", "Intercom"))]),
    ]
    dimensions = []
    for name, criteria in groups:
        low = sum(c["weight"] for c in criteria if c["state"] == "met")
        unknown = sum(c["weight"] for c in criteria if c["state"] == "unknown")
        dimensions.append({"name": name, "low": low, "high": low + unknown,
                           "coverage": 100 - unknown, "criteria": criteria})

    if lead.get("conflict"):
        route, rule, reason = "Manual review", "R1", "Conflicting evidence needs a person to resolve it before routing."
    elif v.get("support_workflow") is False:
        route, rule, reason = "Nurture", "R2", "The requested workflow falls outside the product's customer-support scope."
    elif v.get("support_workflow") is None or v.get("intent") is None:
        route, rule, reason = "Manual review", "R3", "The use case or buying intent is unknown; there is not enough evidence to route this inquiry."
    elif v["intent"] == "Exploring":
        route, rule, reason = "Nurture", "R4", "The prospect is exploring, with no stated evaluation or pilot request."
    elif v.get("monthly_tickets") is None:
        route, rule, reason = "Manual review", "R5", "Monthly support volume is unknown, so the appropriate service tier is unresolved."
    elif v["monthly_tickets"] < 1000:
        route, rule, reason = "Self-service", "R6", "Support volume is below the 1,000-ticket threshold for an assisted evaluation."
    elif (v.get("stack") not in ("Zendesk", "Intercom") or v.get("kb_ready") is not True
          or v.get("implementation_owner") is not True):
        route, rule, reason = "Technical discovery", "R7", "There is evaluation intent and sufficient volume, but integration or implementation readiness needs discovery."
    else:
        route, rule, reason = "Enterprise sales", "R8", "An active evaluation, sufficient support volume, a supported help desk, and stated readiness justify a sales handoff."

    unknowns = [FIELDS[key] for key in FIELDS if v.get(key) is None]
    questions = []
    if lead.get("conflict"):
        questions.append("Which source reflects the current situation? Resolve: " + lead["conflict_note"])
    if v.get("support_workflow") is not True:
        questions.append("Which customer-support workflow would you like to improve?")
    if v.get("monthly_tickets") is None:
        questions.append("How many customer-support tickets do you handle each month?")
    if v.get("stack") not in ("Zendesk", "Intercom"):
        questions.append("Which help desk do you use, and what integration access is available?")
    if v.get("kb_ready") is not True:
        questions.append("Do you have an approved knowledge base, and who can maintain it?")
    if v.get("implementation_owner") is not True:
        questions.append("Who could own the technical setup and pilot evaluation?")
    if v.get("timeline_days") is None:
        questions.append("When would you want to begin an evaluation?")
    questions.append("What measurable outcome would make a pilot successful, and who approves the purchase?")
    return {"route": route, "owner": OWNERS[route], "rule": rule, "reason": reason,
            "dimensions": dimensions, "unknowns": unknowns, "questions": questions,
            "coverage": round(100 * (len(FIELDS) - len(unknowns)) / len(FIELDS)),
            "rubric_version": RUBRIC_VERSION,
            "brief": "{} → {}. {}".format(lead["company"], route, reason)}


def baseline(lead):
    """Deliberately simple comparator: use case, keyword intent, then volume."""
    v = values(lead)
    if v.get("support_workflow") is False:
        return "Nurture"
    if not any(term in lead["inquiry"].lower() for term in ("demo", "pilot", "evaluate")):
        return "Nurture"
    if (v.get("monthly_tickets") or 0) < 1000:
        return "Self-service"
    return "Enterprise sales"
