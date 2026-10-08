# SignalDesk: a configurable lead qualification workflow

Inbound qualification combines three different questions: is this company a plausible customer, is there an active buying conversation, and can the team implement the product? A single opaque score can hide which question needs work.

SignalDesk lets a GTM operator define these criteria for a product, bring their own leads, inspect supporting evidence, and choose a next step. Its intended user is a small B2B revenue team or consultant working through an inbound queue. The local-first distribution lets a GitHub visitor use their own data and API account without depending on the portfolio author's hosted service.

## Product decisions

**Policy belongs to the operator.** The app starts empty. Operators define product capabilities, prerequisites, ideal customers, mandatory criteria, weights, thresholds, and ownership. Saved runs retain the policy used at the time, so a later policy change cannot silently rewrite past decisions.

**Evidence extraction and routing have different responsibilities.** A model proposes criterion assessments with source quotes. Code verifies allowed source types and quote presence, then applies the configured routing policy. Reviewers can override the result with a reason. Model output never directly chooses the route.

**Public information has limits.** Website research can support customer fit. It cannot establish a company's budget, purchasing intent, authority, timeline, or internal implementation access. Intent and readiness require inquiry/notes evidence. Unknown facts remain visible as score ranges and discovery questions.

**Execution needs operational controls.** Batch size is bounded; failed leads do not erase successful work. Account and quota errors stop subsequent requests. Cancellations skip remaining leads. Restarted processes do not resume paid requests automatically. Source snapshots, provider metadata, and review history make individual decisions inspectable.

**Export is the first integration.** CSV, JSON, Markdown briefs, and a CLI allow the output to fit into existing workflows without immediately taking on CRM credentials or write access.

## What the project demonstrates

- PMM: a defined buyer/problem, explicit product positioning and customer criteria, and a clear first-use experience.
- GTM engineering: ingestion, public research, model integration, deterministic policy, data persistence, and useful exports.
- AI deployment: constrained output, citation checks, unknown handling, human review, cost visibility, and failure recovery.
- Strategy and operations: transparent qualification policy, routing ownership, version history, and an evaluation plan tied to decisions.

These are demonstrated implementation choices, not evidence that the tool has generated pipeline or improved conversion. Source-quote validation does not establish semantic correctness. A human-labeled held-out evaluation and a live model smoke test remain necessary before making performance claims. See [evaluation.md](evaluation.md).
