# Bill Guard

Bill Guard is a read-only, Australian-focused invoice checker. It normalises a
bill, runs evidence-backed checks, and returns one of `SAFE TO PAY`, `QUERY`,
`HOLD`, or `NOTHING TO PAY`. The deterministic core uses only the Python 3
standard library and represents checks it cannot complete as `unknown`, never
as a silent pass.

## Run it from the repository

There is no install step. Use Python 3. The entry point resolves its package
relative to itself, so these commands run from a clean checkout. The final
command either decodes the fixture or reports that its optional decoder is
unavailable:

```bash
python3 scripts/billguard.py --help
python3 scripts/billguard.py capabilities
python3 scripts/billguard.py check examples/invoice-clean.json --json
python3 scripts/billguard.py read examples/invoice-clean.json --json
python3 scripts/billguard.py scan-codes examples/intake/two-payment-codes.png --json
```

`check` exits 0 for safe/settled, 1 for query, 2 for hold, and 3 when the
document cannot be read. A nonzero assessment exit is a verdict, not a crash.
Run `python3 scripts/billguard.py selftest` to execute the offline test suite.

Other CLI surfaces are discoverable through `--help`: `paid` records a payment
you say has already happened, `ledger` inspects or updates local history, and
`scheduled-run` assesses an inbox and writes a JSON digest. None of them can
initiate or approve a payment.

## Capability list

Always run `capabilities` on the machine doing the work; it reports registered
checks and the barcode decoders actually available there.

Available without third-party Python packages:

- prepared, versioned JSON document intake;
- plain-text and pasted-text normalisation with per-field confidence;
- checks spanning identity, relationship, payment, duplicates, document
  integrity, channel, scam, sanctions, legal/tax document rules, and deadlines;
- integer-cent arithmetic, pass/fail/unknown evidence, local SQLite history,
  declarative rule packs, scheduled-folder processing, and JSON output;
- equivalent library, agent-tool, CLI, and scheduled-runner verdict contracts;
- free ABR, BSB-directory, and sanctions lookup adapters with timeouts, dated
  local caching, source attribution, and explicit `unknown` fallback.

Optional local capabilities:

- PDF text extraction requires the free `pdftotext` executable;
- scanning every page of a PDF for codes requires the free `pdftoppm`
  executable plus a supported barcode decoder;
- image/code decoding requires OpenCV (`cv2`) or `zbarimg`;
- scanned-document text recognition is not bundled. If a PDF has no text layer,
  Bill Guard reports the capability gap instead of treating it as blank.

The registry clients are not used unless a caller supplies/configures them.
When used, they make read-only requests to the relevant free lookup source and
store dated responses in the chosen local cache. Invoice and ledger data is not
sent to any paid or hosted Bill Guard service; there is no such service.

## What it cannot do

- It cannot prove that a bank account belongs to the named supplier. Treat the
  bank's own account-name confirmation and an independently verified contact as
  separate controls.
- It cannot pay, approve/reject, move, rename, or delete an invoice, log into a
  bank, navigate authenticated links, write to accounting software, or contact
  a supplier.
- It does not provide authoritative legal, tax, sanctions, registry, or fraud
  advice. Findings are decision support with visible source and date evidence.
- It does not guarantee a document is genuine or safe merely because the
  verdict is `SAFE TO PAY`; that means no material issue was found by the checks
  that had enough evidence to run.
- It is focused on Australian invoices, ABNs, GST, BSBs, and configured
  Australian deadline rules. It is not a rules engine for other jurisdictions.
- It cannot promise complete PDF, image, QR, or live-registry coverage when the
  corresponding optional local tool or free source is missing or unavailable.

## Data and safety

The ledger and lookup cache are local SQLite files at paths selected by the
caller. The scheduled runner writes only its configured ledger and digest. The
core has no payment or supplier-contact capability. Treat invoice files as
untrusted input and inspect `unknown`, `QUERY`, and `HOLD` evidence before acting.

## Importable API

Add the repository's `scripts` directory to `PYTHONPATH` (or insert it in the
embedding application), then import `billguard`. The public entry points are
`billguard.assess(document, ledger=None, ctx=None)` for a `Document` and
`billguard.check_json(value, ledger=None, ctx=None)` for the versioned JSON
contract. `check_json` returns a JSON-serialisable object containing the same
verdict and exit semantics as the CLI and never performs an action on the bill.

Rule packs live in `packs/`; `packs/construction.json` demonstrates validated
progress-claim and retention rules. See `SKILL.md` for the portable skill
contract and agent-facing safety guidance.
