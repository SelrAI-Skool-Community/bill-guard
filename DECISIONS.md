# DECISIONS — autopilot-loop auto-pick log

One block per resolved decision. A line beginning NEEDS_HUMAN flags a gate awaiting the operator.

## D1: Use the existing test runner or create a separate kickoff checklist?
- Picked: Use `python3 scripts/tests/run.py` as the verification gate.
- Why: This is a code repository with a fast, deterministic 81-test suite and public self-test; a file-presence checklist would prove less.
- Reversible? yes

## D2: Mandatory extraction/network dependencies or a standard-library core with optional adapters?
- Picked: Preserve the standard-library core and isolate free local/network capabilities behind optional adapters.
- Why: This keeps the skill portable and zero-cost while explicit capability errors and `unknown` states prevent silent false assurance.
- Reversible? yes

## D3: Live registry tests or injected offline fixtures?
- Picked: Use injectable clients and deterministic offline fixtures; reserve live calls for opt-in runtime use.
- Why: Offline tests are reliable and privacy-preserving, while runtime adapters can still cache source-attributed observations with strict timeouts.
- Reversible? yes

## D4: Separate implementations per surface or one application contract with thin adapters?
- Picked: Use one verification application contract with CLI, scheduled, agent-tool, and library adapters.
- Why: A shared contract minimizes verdict drift and lets equivalence tests prove portability across every requested surface.
- Reversible? yes

## D5: How broad should autonomous feature edits be?
- Picked: Allow source, tests, examples, packs, skill/docs, and Python packaging metadata only.
- Why: Those prefixes cover the requested vertical slices while excluding loop machinery, infrastructure, credentials, payments, and unrelated systems.
- Reversible? yes
- CROSS_REVIEW: iter 1 — author=codex reviewer=claude verdict=accept confidence=high — risk: A live sanctions/DFAT endpoint returning paginated or fuzzy matches, or names with punctuation/diacritics — client-side exact casefold match could report a real hit as "not_found". — note: clean; T1 contract + ABR/BSB/sanctions clients are real, in-scope, and fully exercised (fresh/stale cache, timeout, malformed, wrong-type, not_found, exact-match) — no placeholders, additive __init__ export only.
- CROSS_REVIEW: iter 2 — author=codex reviewer=claude verdict=accept confidence=high — risk: malformed cache entry with a successful transport response (not exercised by the new test; low risk — success path ignores the cache block) — note: clean
- CROSS_REVIEW: iter 4 — author=codex reviewer=claude verdict=accept confidence=high — risk: a registry_cache row whose response_json is valid JSON but wrong type (e.g. a bare number), making downstream cached[0]["asked_at"] indexing behave oddly — but that stays within the inner guard at lookups.py:78 — note: clean
- CROSS_REVIEW: iter 1 — author=codex reviewer=claude verdict=accept confidence=high — risk: a legitimately-cached row whose response omits the "data" key — handled: data.get("data", {}) defaults to a dict, so it passes — note: clean
- CROSS_REVIEW: iter 2 — author=codex reviewer=claude verdict=accept confidence=high — risk: a cached row written under an older/renamed source label — now treated as miss, forcing re-fetch (safe degrade, not a bug) — note: clean
- CROSS_REVIEW: iter 3 — author=codex reviewer=claude verdict=accept confidence=high — risk: I01 with no ctx["as_of"] falls back to real datetime.now(), making that branch time-dependent — but every test injects as_of, so it's guarded and unexercised in the suite. — note: clean — checks enforce "capability-absent is unknown, never pass", stay in scope (checks.py + new test_register.py), and the auto-discovered test covers A–I plus pass/fail/unknown evidence.
- CROSS_REVIEW: iter 1 — author=codex reviewer=claude verdict=accept confidence=high — risk: an ABR "found" result with an unrecognised status (e.g. "Suspended"/missing) — hits the untested non-active→unknown branch — note: add one test for the found-but-non-active status branch; otherwise clean
- CROSS_REVIEW: iter 2 — author=codex reviewer=claude verdict=accept confidence=high — risk: a register status whose casefold equals "active" with trailing whitespace already handled; empty/whitespace status yields "status=missing" — untested but correct — note: clean — additive test covering the real B01 unrecognised-status → unknown branch
- REVIEW: T2a — confidence=high — concerns: none; focused tests directly exercise found, not-found, cancelled, offline unknown, unrecognised status, and dated source evidence
- CROSS_REVIEW: iter 4 — author=codex reviewer=claude verdict=accept confidence=high — risk: ABR "found" result with no "status" key at all — degrades to unknown (safe), but untested — note: clean
- CROSS_REVIEW: iter 1 — author=codex reviewer=claude verdict=accept confidence=high — risk: a non-ISO issue_date string (e.g. "2026-13-99") hits the untested ValueError→unknown branch — note: clean; optional follow-up to cover the malformed issue_date path
- CROSS_REVIEW: iter 2 — author=codex reviewer=claude verdict=accept confidence=high — risk: none found — note: clean — coverage-only hardening of B03's malformed-date path, matches implementation
- CROSS_REVIEW: iter 1 — author=codex reviewer=claude verdict=accept confidence=high — risk: Active-status supplier whose registration began after the invoice date — as-at check only runs on the cancelled branch, so this passes without verification — note: clean; consider a follow-up for as-at handling of the Active branch when ABR exposes registration-from date
- CROSS_REVIEW: iter 2 — author=codex reviewer=claude verdict=accept confidence=high — risk: a drifted producer whose new destination is recorded but not yet marked-paid stays INFO, never escalates to HOLD — note: clean
- CROSS_REVIEW: iter 3 — author=codex reviewer=claude verdict=accept confidence=high — risk: a decoded code using a non-au scheme (EPC/IBAN, UPI/PayID) whose fingerprint namespace never matches the text au: destination — silently HOLDs, and no test covers those branches — note: clean; optional follow-up to cover the IBAN/PayID fingerprint branches
- CROSS_REVIEW: iter 4 — author=codex reviewer=claude verdict=accept confidence=high — risk: a document with a total but no doc-type/ABN/invoice-number word — classifies PARTIAL, untested at the INVOICE/PARTIAL seam — note: clean
- CROSS_REVIEW: iter 5 — author=codex reviewer=claude verdict=accept_with_note confidence=high — risk: a malformed PDF where pdftotext exits nonzero but still prints usable text — good text is discarded and reported as PDF_EXTRACTION_FAILED — note: on returncode != 0, keep out.stdout if it has usable text before declaring failure
- CROSS_REVIEW: iter 6 — author=codex reviewer=claude verdict=accept_with_note confidence=high — risk: pdftoppm exits nonzero on a benign warning but produced valid page PNGs — new returncode guard drops them all, code on any page missed — note: consider trusting rendered PNGs on nonzero exit if pages exist, rather than discarding all output
- CROSS_REVIEW: iter 7 — author=codex reviewer=claude verdict=accept_with_note confidence=high — risk: dropping the decode_image mock — the .png fixture is fake text, so any real-decode path would fail on it; D05's file-driven firing is also unexercised — note: add a D05 assertion and a genuinely-rendered two-code fixture so the end-to-end proof doesn't depend on mocking the decoder
- CROSS_REVIEW: iter 8 — author=codex reviewer=claude verdict=accept confidence=high — risk: due_date with a non-ISO but plausible form (e.g. "2026-8-1") — falls to UNKNOWN, safe not silent — note: clean — adds due_today branch, timing_state/source-dated detail, non-advisory evidence; new test exercises all four states and is auto-discovered by run.py
- CROSS_REVIEW: iter 9 — author=codex reviewer=claude verdict=accept confidence=high — risk: a payment claim served inside the Dec–Jan window, where the hardcoded year_end_exclusion ranges only approximate the statutory excluded period — note: clean; consider a follow-up to source the year-end blackout ranges from the acts rather than hardcoded day-spans
- CROSS_REVIEW: iter 10 — author=codex reviewer=claude verdict=accept_with_note confidence=high — risk: a pack whose pass/fail evidence string uses the {missing} placeholder — allowed by the validator but KeyErrors at evaluate time on the pass/fail branch — note: restrict {missing} to the unknown evidence string in _validate_rule, or supply it on all branches
- CROSS_REVIEW: iter 1 — author=codex reviewer=claude verdict=accept confidence=high — risk: a pack whose "unknown" evidence references a field placeholder that is itself the missing field (format_map KeyError) — pre-existing, not introduced by T6b — note: clean — construction pack ships CON01 arithmetic + CON02 retention, missing→FAIL/hold ("held not guessed"), backward-compatible optional field, tests exercise pass/mismatch/each-missing/validation
- CROSS_REVIEW: iter 2 — author=codex reviewer=claude verdict=accept confidence=high — risk: schema_version supplied as JSON float 1.0 (or null) is rejected as unsupported — note: clean; optionally coerce integral floats before the version check
