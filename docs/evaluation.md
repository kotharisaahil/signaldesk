# Verification status

Checked on October 7, 2026. This report separates software behavior from model and business performance.

## Automated checks

Run `python3 -B -m unittest discover -s tests -v` from the project root. Tests require no provider key or paid requests.

**72 tests passed:** 47 for the reusable application and 25 legacy regression checks. JavaScript syntax validation and a CLI configure/import/deduplicate/list/export roundtrip also passed.

The current application suite covers configurable profiles, required criteria, threshold boundaries including rounding, unknowns, conflicts, invalid quotes, source restrictions, CSV validation, deduplication, immutable runs, reviewer history, staleness, exports, request protection, structured provider requests, failures, cancellation, restart behavior, key handling, and bounded website fetching with mocked network responses. The earlier demo's 25 regression checks are retained separately.

These checks exercise a working local HTTP server and disposable SQLite databases. Provider mocks establish the contract and failure behavior, not live model quality.

## Manual and live checks

- Browser: a new analytics product was configured, a custom lead added, all six criteria assessed with quotes, and the result routed to Sales ready. A reviewer override to Discovery was saved with a reason in an isolated test database.
- Website research: a live request to `https://www.python.org/` returned a readable 9,316-character source snapshot with no research warnings.
- Browser CSV intake: a two-row file previewed successfully and imported; the existing custom lead and review remained intact. The missing-key run action opened AI connection without creating a qualification.
- Responsive layout: connection and lead-detail pages had no horizontal overflow at a 390-pixel viewport.
- API: no real paid model call was made because no API key was available. This remains an explicit unverified boundary.

## Before claiming model performance

Create a held-out set of real or independently authored inquiries. Have a human label each criterion and route before running the model. Keep ambiguous cases, missing evidence, conflicting notes, and adversarial instructions in the set. Report criterion precision/recall, unsupported assessment rate, appropriate unknown handling, route confusion, override rate, tokens, latency, and cost. Compare against a simple rules-only baseline and manual review time.

Do not interpret test pass rate or the archived demo's 12/12 authored fixture agreement as model accuracy. Conversion lift, production adoption, and time savings have not been measured.
