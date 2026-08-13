# QUESTIONS — autopilot-loop recommendation queue

The loop writes recommended questions here, chooses safe answers, then turns the chosen item into `PLAN.md` work.

Line format:

```text
- [ ] Q0001 | gate=safe_now | impact=high | risk=low | reversible=yes | paths=src/foo tests/foo | verify=npm test | question=Cache results in memory or on disk? | recommended=In memory | reason=Read-heavy, small payloads; disk adds I/O for no gain.
```

Question rules (the grill-me bar — every question must clear it):
- **One named fork** — exactly two named options (A vs B), never open-ended ("what should improve next?" is banned).
- **`recommended=`** — the chosen option, stated plainly.
- **`reason=`** — one line: why this option, and why the alternative loses. Mandatory. A question without a reason may not be picked.
- **Codebase-grounded** — if reading the repo answers it, read the repo instead of queueing it.

Pick order is MECHANICAL, not a judgment call: sort `gate=safe_now` by impact desc (high > med > low), then risk asc (low first), then declared order. Take the top item. Always.

Gate values:
- `safe_now`: the loop may choose and act.
- `needs_human`: money, legal, destructive, data, or business-scope call.
- `blocked`: cannot be verified with the current repo/tools.
- `picked`: already converted into `PLAN.md` or `DECISIONS.md`.

## Open

## Picked

## Needs Human
