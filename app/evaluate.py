"""A reproducible fixture check. This is not an independent model benchmark."""
import json
from pathlib import Path
from .qualification import baseline, qualify

ROOT = Path(__file__).resolve().parents[1]


def evaluate():
    leads = json.loads((ROOT / "data" / "leads.json").read_text())
    expected = json.loads((ROOT / "data" / "expected_routes.json").read_text())
    cases = [{"id": lead["id"], "company": lead["company"], "expected": expected[lead["id"]],
              "route": qualify(lead)["route"], "baseline": baseline(lead)} for lead in leads]
    return {"total": len(cases), "correct": sum(c["route"] == c["expected"] for c in cases),
            "baseline_correct": sum(c["baseline"] == c["expected"] for c in cases),
            "manual_review": sum(c["route"] == "Manual review" for c in cases), "cases": cases,
            "limitation": "Authored demo fixtures, not held-out evidence of real-world performance. No live AI extraction."}


def report(result):
    lines = ["# Demo fixture evaluation", "", result["limitation"], "",
             "- Explicit playbook: {}/{} expected routes matched.".format(result["correct"], result["total"]),
             "- Simple baseline: {}/{} expected routes matched.".format(result["baseline_correct"], result["total"]),
             "- Manual-review recommendations: {}.".format(result["manual_review"]),
             "- AI API cost: $0; no model calls.", "",
             "The baseline uses use-case fit, inquiry keywords (demo, pilot, evaluate), and the 1,000-ticket threshold. It ignores readiness and evidence conflicts. Both methods consume prepared facts, not live extraction.", "",
             "| Scenario | Expected | Playbook | Baseline |", "|---|---|---|---|"]
    lines.extend("| {company} | {expected} | {route} | {baseline} |".format(**case) for case in result["cases"])
    lines += ["", "## Interpretation", "", "Matching these fixtures checks implementation consistency. The rules and expectations were developed together, so this is not an independent accuracy estimate. The baseline was selected as a simple comparison, not an optimized competitor.", "", "## Next validation step", "", "Have a human reviewer label fresh cases before running the workflow. Hold those cases out from tuning. Assess per-route mistakes, manual-review rate, unsupported extracted claims, and measured costs. Real conversion lift and time savings remain unmeasured."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Save docs/legacy-demo-evaluation.md")
    args = parser.parse_args()
    text = report(evaluate())
    if args.write:
        (ROOT / "docs" / "legacy-demo-evaluation.md").write_text(text)
    print(text)
