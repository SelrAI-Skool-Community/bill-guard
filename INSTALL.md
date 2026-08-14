# Install Bill Guard

Paste this into a new Claude Code session:

```
Install the bill-guard skill for me, following the install steps in
https://github.com/luke-heka/bill-guard
```

Claude will clone the repo, run the test suite, and show you the result.

## What Claude does

1. Clones this repo to `~/Projects/bill-guard`
2. Runs `python3 scripts/tests/run.py` and confirms every test passes
3. Adds a `billguard` shortcut to your shell so you can type it anywhere
4. Runs `billguard capabilities` and tells you what your machine can do
5. Points you at the README and stops

Nothing is installed system-wide. No packages, no account, no API key. The
whole thing is Python's standard library plus files in one folder.

## Then

```bash
billguard check that-invoice.pdf --ledger ~/billguard.db --remember
billguard paid that-invoice.pdf --ledger ~/billguard.db
```

Check bills as they arrive, and run `paid` when you pay one. After a few
invoices from a supplier it knows what normal looks like for them, and a
changed bank account stands out immediately.

## Verify it yourself

```bash
billguard selftest
```

Every test runs offline. Nothing about your invoices leaves your machine.
