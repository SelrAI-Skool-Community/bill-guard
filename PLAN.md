# PLAN — robust, portable, zero-cost Bill Guard

Tasks are deliberately SMALL. The first run stalled because T1 bundled three
clients, a cache, timeouts, attribution and fakes into one task: the work got
done but the box was never ticked, so the stagnation breaker killed the run
with everything green. One task should be one commit.

## Done
- [x] T1: Lookup adapter contract, ABR + BSB + sanctions clients, dated cache,
      timeouts, source attribution, offline fakes. VERIFIED 2026-08-14: success,
      cache hit without re-fetch, timeout to `unknown`, malformed to `unknown`,
      and a dated row in `registry_cache` carrying `observed_at`.

## Critical path
- [x] T2a: Wire the ABR client into check B01 so a present ABN is confirmed
      against the register — done when: tests cover found, not-found, cancelled,
      and the offline `unknown` path, and the evidence names the register and
      the date it was asked.
- [x] T2b: Add check B03, supplier registration status AS AT THE INVOICE DATE,
      not today — done when: a supplier cancelled after the invoice date passes,
      one cancelled before it fails, and a missing date returns unknown.
- [x] T2c: Add family E, document integrity: producer-tool drift against this
      supplier's own history — done when: drift alone is INFO, and drift plus a
      changed payment destination is a HOLD.
- [x] T2d: Add check D06, payment destination cross-checked against the decoded
      code payload — done when: text and code agreeing passes, disagreeing holds,
      and a code with no destination returns unknown.

- [x] T3a: Intake: plain text and pasted-block normaliser producing a Document
      with per-field confidence — done when: fixtures for a clean invoice, a
      truncated one, and rubbish each land in exactly one outcome.
- [x] T3b: Intake: PDF text-layer extraction via pdftotext, with a named failure
      when there is no text layer — done when: a text PDF extracts and a scanned
      one reports a capability gap rather than an empty pass.
- [x] T3c: Intake: image and PDF page rendering feeding the code decoder, every
      page not just the first — done when: a fixture hiding a code on the last
      page is still found.

- [x] T4a: Attach decoded codes to the Document as artifacts during intake so
      D04 and D05 fire from real files — done when: a two-code fixture holds
      end to end through the CLI.

- [x] T5a: Family I clocks: due-date arithmetic with an injectable "as at" date —
      done when: upcoming, due today, overdue and missing-date all resolve, and
      no legal advice is asserted.
- [ ] T5b: Family I: business-day deadline arithmetic with a jurisdiction table —
      done when: a payment claim's reply deadline is computed for two states and
      the source of each rule is named in the evidence.

- [ ] T6a: Declarative niche pack loader with schema validation — done when: a
      valid pack loads, an invalid one is rejected with a named reason, and pack
      findings carry the same evidence shape as built-in checks.
- [ ] T6b: Ship the construction pack: progress-claim arithmetic and retention —
      done when: claimed-to-date minus previously-certified must equal this claim,
      and a claim missing any of the three is held rather than guessed.

- [ ] T7a: Freeze the JSON document contract and version it — done when: a
      contract test asserts the shape and an unknown field is ignored, not fatal.
- [ ] T7b: Agent-tool adapter: a single function taking JSON and returning the
      verdict JSON — done when: the same fixture through library, CLI and adapter
      produces identical verdicts and identical exit semantics.
- [ ] T7c: Scheduled-runner adapter: process a folder, write a digest, never act —
      done when: a run over a fixture folder produces a digest and provably makes
      no write outside its own ledger and output file.

## Independent
- [ ] T8a: README with the honest capability list, including what it cannot do —
      done when: every command shown is exercised by a test.
- [ ] T8b: Packaging smoke test from a clean directory — done when: importing
      `billguard` and running the CLI both work with no install step.
