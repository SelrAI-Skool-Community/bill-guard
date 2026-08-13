# Bill Guard

Check an invoice before you pay it.

You point it at a bill. It tells you **safe to pay**, **query**, or **hold**,
and says why in plain words.

It runs on your own computer. No account, no subscription, no API key, and
nothing about your invoices is sent anywhere.

---

## The one thing it is for

Somebody emails you an invoice that looks exactly right. Same supplier, same
logo, same amount. One thing changed: the bank account.

That is how businesses actually lose money, and it is the thing Bill Guard
watches. It remembers which account you have really paid each supplier at,
and holds anything that names a different one.

Everything else it does is supporting evidence.

---

## Setup

Nothing to install. You need Python, which every Mac already has.

```bash
python3 scripts/billguard.py capabilities
```

If that printed something, you are done.

To type `billguard` instead of the long path, add this line to your
`~/.zshrc`, then open a new terminal:

```bash
alias billguard="python3 $HOME/Projects/bill-guard/scripts/billguard.py"
```

Pick a place to keep its memory. Any path will do; it makes the file itself.

```bash
export BILLGUARD_LEDGER=~/billguard.db
```

---

## Using it

**Check a bill.** Works on a PDF, a photo, a saved email, a text file, or
text you paste.

```bash
billguard check invoice.pdf --ledger ~/billguard.db --remember
```

**Tell it when you actually pay something.** Just point at the same file.
This is what makes the bank-account check work, so it is worth doing every
time.

```bash
billguard paid invoice.pdf --ledger ~/billguard.db
```

**See what it read off a document**, when you want to check its working:

```bash
billguard read invoice.pdf
```

**Paste text instead of a file:**

```bash
pbpaste | billguard check -
```

**Scan a barcode on a page**, and find out where the money would really go:

```bash
billguard scan-codes invoice.png
```

---

## What the answers mean

**SAFE TO PAY** — nothing material changed, and every check that could run
passed.

**QUERY** — something needs a person to look before paying. It says what.

**HOLD** — do not pay yet. It names the reason in one sentence and tells you
what to do about it.

**NOTHING TO PAY** — this is a receipt for something already paid, not a
bill. File it.

It also prints a **worth knowing** section for paperwork problems, like an
invoice that will not support a GST claim. Those never stop you paying.

For automation, the exit code is the answer: `0` safe or nothing to pay,
`1` query, `2` hold, `3` could not read it.

---

## The first week

The first time it sees a supplier it will say so, because it has no history
to compare against. That is not a warning about them, it is the tool being
honest that it cannot help yet.

It gets useful as you use it. Check bills as they arrive, and run `paid`
when you pay one. After a few invoices from a supplier it knows what normal
looks like for them, and a changed bank account stands out immediately.

---

## What it cannot do

**It cannot tell you whether a bank account really belongs to that business.**
That needs a bank's own systems. Every verdict says so.

When you go to pay, your banking app runs its own account-name check and
tells you whether the name matches the account. That is the control that
actually catches a redirected payment. Read what it says.

**It cannot pay, approve, or send anything.** There is no code in here that
can move money or email anyone. That is deliberate: an invoice is a document
a stranger sent you, and a tool that reads strangers' documents should not
also be able to act on your accounts.

**It cannot read a scan or a photo of paper** unless you install text
recognition separately. It will say so rather than pretending the page was
blank.

**It is written for Australian invoices.** ABNs, GST, BSBs. It will still
check bank-account changes, duplicates and arithmetic anywhere, and it
knows not to demand an ABN from an overseas supplier.

---

## Where your data lives

One file, wherever you put it, on your machine. Nothing is uploaded. Bill
Guard makes no network connection at all unless you deliberately turn on
business-register lookups.

---

## Checking it works

```bash
billguard selftest
```

Runs the full test suite offline. Everything should pass.
