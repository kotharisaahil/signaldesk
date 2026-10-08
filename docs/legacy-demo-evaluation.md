# Demo fixture evaluation

Authored demo fixtures, not held-out evidence of real-world performance. No live AI extraction.

- Explicit playbook: 12/12 expected routes matched.
- Simple baseline: 6/12 expected routes matched.
- Manual-review recommendations: 3.
- AI API cost: $0; no model calls.

The baseline uses use-case fit, inquiry keywords (demo, pilot, evaluate), and the 1,000-ticket threshold. It ignores readiness and evidence conflicts. Both methods consume prepared facts, not live extraction.

| Scenario | Expected | Playbook | Baseline |
|---|---|---|---|
| Northstar Commerce | Enterprise sales | Enterprise sales | Enterprise sales |
| Orbit Analytics | Enterprise sales | Enterprise sales | Enterprise sales |
| Fieldwork Supply | Self-service | Self-service | Self-service |
| Cedar Studio | Self-service | Self-service | Self-service |
| Atlas Logistics | Technical discovery | Technical discovery | Enterprise sales |
| Lumen Learning | Technical discovery | Technical discovery | Enterprise sales |
| Harbor Retail | Technical discovery | Technical discovery | Enterprise sales |
| Summit Cloud | Nurture | Nurture | Nurture |
| Pixel Forge | Nurture | Nurture | Nurture |
| Juniper Health Tech | Manual review | Manual review | Self-service |
| Signal Works | Manual review | Manual review | Nurture |
| Meridian Market | Manual review | Manual review | Self-service |

## Interpretation

Matching these fixtures checks implementation consistency. The rules and expectations were developed together, so this is not an independent accuracy estimate. The baseline was selected as a simple comparison, not an optimized competitor.

## Next validation step

Have a human reviewer label fresh cases before running the workflow. Hold those cases out from tuning. Assess per-route mistakes, manual-review rate, unsupported extracted claims, and measured costs. Real conversion lift and time savings remain unmeasured.
