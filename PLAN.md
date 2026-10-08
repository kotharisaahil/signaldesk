# SignalDesk implementation plan

The deliverable is a tool a visitor can download and use with their own product and leads.

## Implemented

- Empty, configurable workspace with product/ICP definitions, weighted criteria, thresholds, and owners.
- CSV preview/import, deduplication, manual lead entry, profile import/export.
- Bounded public website retrieval with sources, timestamps, robots rules, and network restrictions.
- OpenAI structured-output integration with a private local key field, token metadata, and explicit execution consent.
- Evidence validation, deterministic routing, unknowns/conflicts, and manual assessments.
- Sequential batch processing, partial results, cancellation, account-error stops, and restart recovery.
- Persistent runs and reviews, old-policy indicators, CSV/JSON/Markdown exports, command-line access.
- Automated tests, browser workflow verification, setup documentation, reuse license, and CI configuration.

## Remaining validation

1. Connect the owner's API key privately in the application and qualify a non-sensitive inquiry. Verify the live response, citations, and recorded usage.
2. Label independent real-world cases before tuning. Measure unsupported claims, route errors, review time, and provider cost.
## GitHub distribution

The public source repository is [kotharisaahil/signaldesk](https://github.com/kotharisaahil/signaldesk). It includes setup instructions, an MIT license, and automated tests. Local workspaces and credentials are excluded.

## Later product work

Prioritize lead editing/archiving and a CRM export mapping after observing actual usage. Public multi-user hosting, CRM writes, and outbound outreach require a separately scoped deployment. Avoid claiming measured business impact before it has been evaluated.
