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
