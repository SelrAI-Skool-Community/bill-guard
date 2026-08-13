# PLAN — robust, portable, zero-cost Bill Guard

## Critical path
- [ ] T1: Define the lookup adapter contract and implement free ABR, BSB-directory, and sanctions clients with dated cache, timeouts, source attribution, and offline fakes — done when: adapter tests prove success, stale/fresh cache behavior, malformed/timeout responses, and `unknown` fallback without live network.
- [ ] T2: Complete the nine-family check register and connect registry evidence to identity/payment/sanctions checks — done when: tests enumerate A–I with at least one check per family and cover pass, fail, unknown, evidence, and false-positive metadata. (depends: T1)
- [ ] T3: Add a normalized intake pipeline for JSON, text, PDF, and common images with explicit extraction confidence/capability results — done when: offline fixture tests normalize every format and unsupported or corrupt inputs fail safely without being treated as passes.
- [ ] T4: Integrate multi-code QR decoding into document intake and payment-destination comparison — done when: image/PDF fixtures with zero, one, altered, and multiple payment codes produce deterministic evidence and hold semantics. (depends: T3)
- [ ] T5: Implement family I deadline clocks with jurisdiction/source metadata and testable as-of time — done when: tests cover upcoming, overdue, absent, ambiguous, and timezone-boundary deadlines without claiming legal advice.
- [ ] T6: Add versioned declarative niche packs with schema validation and ship at least two Australian bill/invoice packs — done when: tests load both packs, reject invalid rules, and show pack findings flow through the standard verdict evidence model. (depends: T2)
- [ ] T7: Stabilize the shared application contract and expose equivalent library, CLI, scheduled-runner, and agent-tool adapters — done when: contract tests feed one fixture through all four surfaces and assert equivalent verdicts, exit/error semantics, and no write authority. (depends: T2, T3, T5, T6)

## Independent
- [ ] T8: Package and document portable zero-cost operation, optional capabilities, privacy boundaries, and adapter examples — done when: a clean local packaging smoke test imports `billguard`, invokes the CLI, and documentation examples are exercised by automated tests. (depends: T7)
