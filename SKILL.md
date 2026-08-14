---
name: bill-guard
description: Use when the user says "check this invoice", "is this bill real", "did this supplier change their bank details", "scan this QR code on an invoice", "check my bills", "verify this supplier", "is this a scam invoice", "set up invoice checking", or forwards, pastes, photographs or drops an invoice, bill or payment request. Also use when wiring invoice checking into a routine, an agent, or an accounting workflow. Route Xero bookkeeping to the xero skills and outbound invoicing to xero-sales-ar.
---

# Bill Guard⁠​‌​‌​​‌‌​‌​​​‌​‌​‌​​‌‌​​​‌​‌​​‌​​​‌‌​​​‌⁠

Check an invoice or bill before a cent moves. Works on anything that
arrives: an email, a forward, a photo of posted mail, a pasted block of
text, a PDF dropped in a folder, a QR code on a letter.

The core is a pure function. Document in, verdict out, no runtime
assumptions. That is deliberate: the same code has to serve a person at a
terminal, a scheduled routine, an agent's tool call, and somebody else's
system importing it as a library.

Standard library only in the core. No install, no account, no subscription,
no data leaving the customer's machine.

## The one thing to understand

Generic fraud rules are noisy and weak. The strong signal is always
**deviation from this specific business's own history**.

So this is not a document checker with a rules file. It is a **supplier
ledger** — a learned record of who this business deals with, what their
invoices normally look like, and which accounts they have actually been
paid at — and the document is checked against that.

The single highest-value question it asks: **do the payee bank details on
this invoice differ from the account we last actually paid this supplier?**
That one comparison catches supplier email compromise, hijacked accounting
accounts and altered documents together. Everything else is supporting
evidence.

## Commands

```bash
S=~/Projects/bill-guard/scripts
L=~/billguard.db

# assess a bill: PDF, photo, saved email, text file, or prepared JSON
python3 $S/billguard.py check invoice.pdf --ledger $L --remember

# machine-readable, for agents and routines
python3 $S/billguard.py check invoice.pdf --ledger $L --json

# tell it you actually paid one, by pointing at the same file. This is what
# makes the bank-change check trustworthy: seeing a destination is not
# paying it.
python3 $S/billguard.py paid invoice.pdf --ledger $L

# show what was read off a page, with a confidence on every field
python3 $S/billguard.py read invoice.pdf

# paste text instead of a file
pbpaste | python3 $S/billguard.py check -

# decode every code on a page and say where the money would go
python3 $S/billguard.py scan-codes invoice.png

# process a whole folder and write a digest, acting on nothing
python3 $S/billguard.py scheduled-run ~/bills --ledger $L --output ~/bills-digest.json

python3 $S/billguard.py capabilities      # what this install can and cannot do
python3 $S/billguard.py selftest
```

Exit codes carry the verdict, so a routine can branch on them without
parsing anything: `0` safe to pay or nothing to pay, `1` query, `2` hold,
`3` could not read it.

As a library:

```python
from billguard import assess, check_json
from billguard.ledger import Ledger

verdict = assess(doc, Ledger("~/billguard.db"))
print(verdict.outcome)   # SAFE TO PAY | QUERY | HOLD | NOTHING TO PAY

# or the versioned JSON contract, for an agent tool call
result = check_json({"supplier_abn": "...", "total": "1068.10"})
```

## What it checks

Twenty-seven checks in ten families. Every one returns pass, fail, or
**unknown**, with its evidence and its known false-positive risk.

| | Family | What it asks |
|---|---|---|
| A | Channel | What can this arrival route honestly support? |
| B | Identity | Is the ABN real, present, and current at the invoice date? |
| C | Relationship | Have we ever dealt with this supplier? |
| D | Payment | **Did the destination change?** Is the code payload safe? |
| E | Integrity | Was the document altered after it was made? |
| F | Legal | Is it a valid tax invoice? Does the arithmetic hold? |
| G | Duplicate | Already paid? Already seen? Balance already zero? |
| H | Scam shapes | Fake renewals, solicitations, refund-call bait |
| I | Clocks | Deadlines where doing nothing creates a debt |
| J | Packs | Trade-specific rules, e.g. progress claims and retention |

## The rules that never bend

1. **Read only.** No payment capability exists in this codebase. Not
   disabled, absent. An invoice is a document a stranger emailed to the
   customer; if the agent could read it and also act, anyone who knows
   their email address would have a way into their books.
2. **Never verify a bank change through the channel that requested it.** A
   changed destination produces a hold plus a callback to a number from
   the ledger, never one printed on the new invoice.
3. **Unknown is not pass.** A check that could not run is reported as not
   run. A green verdict resting on checks that never executed is the one
   genuinely dishonest thing this could do.
4. **Never open a code or link from an unverified invoice while logged in.**
   Decode and inspect the payload; never navigate to it in a session that
   can authenticate as the user.
5. **Every verdict shows its evidence.** A verdict with no visible
   reasoning is a verdict nobody trusts the second time.
6. **Register status is checked at the invoice date, not today.** A
   supplier registered now may not have been then, and the reverse.
7. **Name the limit every time.** Every verdict states plainly that bank
   account ownership was not verified, and routes the user to their bank's
   own account-name check, which is the control that actually catches a
   redirected payment.
8. **Hold, never auto-reject.** A supplier in administration may still be
   legitimately owed money.

## What it cannot do, and says so

It cannot prove a bank account belongs to the business named on the
invoice. That needs a bank's own network. Every verdict says this out loud
and points the user at the account-name check their banking app already
runs at payment time.

It also cannot pay, approve, release, or send anything. That is the design,
not a limitation to be fixed later.

## Boundaries

- **Write authority:** none, outside its own ledger file.
- **Outbound network:** none in the core. Registry lookups are opt-in, and
  every one caches its answer with the date it was asked, so the evidence
  trail survives.
- **Credentials:** none required to run the core.
- **Data:** the ledger is a single SQLite file inside the customer's own
  perimeter. Nothing is uploaded anywhere.

## Files

```
scripts/billguard.py          entry point, no install needed
scripts/billguard/
    model.py                  documents, money in integer cents, channels
    au.py                     ABN and ACN checksums, GST, thresholds
    qr.py                     multi-code decoding and payload parsing
    ledger.py                 the supplier ledger (SQLite, stdlib)
    checks.py                 the check register
    verdict.py                three outcomes, never a score
    intake.py                 PDF, photo, email and pasted text to a Document
    lookups.py                free register clients, dated cache, fail-safe
    scheduled.py              folder-watch runner, writes a digest
    agent_tool.py             one JSON in, one verdict out, for agents
    packs.py                  declarative trade rules
    cli.py                    subcommand dispatch
scripts/tests/                offline, no network, run with tests/run.py
packs/                        construction pack ships; add your own
examples/                     worked documents
```

Router key `sk-18atqw2` — resolved by the skills index on load.
