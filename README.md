# supplier-check

A zero-dependency command-line record book for supplier due diligence, built
for small businesses. Before you buy from a new supplier there is a short
list of things worth confirming - GSTIN, physical address, bank account in
the business's name, sample quality, references, payment terms in writing.
This tool does not verify any of that for you. It keeps a dated, noted
record of the checks **you** performed, so "did we ever confirm their bank
account?" has an answer.

Single Python file, standard library only (Python 3.9+). Fully offline; all
data lives in one local JSON file you own.

## Why this exists

On Razorpay's public "Fix My Itch" list, one entry asks: "Why can't
businesses verify new suppliers before purchasing?" (page fetched and entry
re-verified on 2026-11-17; https://razorpay.com/m/fix-my-itch/). That list
is curated by Razorpay - it shows the problem was submitted and published,
not how common it is.

This tool cannot verify a supplier - no software can confirm an address or
judge a sample for you. It solves the follow-on problem a small business
*can* control: doing the checks **consistently** and keeping the proof.
Every outcome (pass / fail / not-applicable) is recorded with a date and a
note, and a supplier only reads as READY once every required check has a
pass (or a deliberate n/a) - and stops being READY the moment any check
fails.

## What it does

- **Supplier registry** - name, contact, optional GSTIN (format-checked),
  notes.
- **A default checklist** of six due-diligence checks, four of them
  required: GSTIN format, address, bank account in the business name,
  written payment terms (required), plus sample quality and trade
  references (optional). Add your own checks with `add-check`.
- **Dated records** - every check outcome with date and note; the latest
  record per check is the current status, so a supplier who failed in the
  past can be re-checked and recover.
- **Verdicts** - `READY`, `MISSING: ...` (required checks not done), or
  `FAILED: ...` (any check failed). A failed check always blocks READY
  until a newer record passes.
- **CSV export** of the full supplier x check matrix for a spreadsheet.

## What it does NOT do (limits)

- **It verifies nothing itself.** It is a record book. The checks happen in
  the real world, done by you.
- **GSTIN handling is a format check only** - 15 characters in the official
  pattern. It does not look up any government registry and says nothing
  about whether the GSTIN is real, active, or belongs to that supplier.
- **Not legal or compliance advice.** The default checklist is a practical
  starting point, not a definition of sufficient due diligence; add your own
  checks for your industry.
- **Not purchasing or inventory.** No orders, invoices, prices, stock, or
  payments (see the sibling tools kitchen-cost-book and invoice-ledger for
  those).
- **Durability:** saves are atomic (write to a temp file, then replace the
  store), and a failed save leaves the previous store intact - that behavior
  is covered by tests. Beyond that there are no guarantees against power
  loss mid-write or two people editing the same file at once.

## Landscape note (checked 2026-11-17)

Dated searches on 2026-11-17 for `supplier verification checklist cli tool
open source` and `vendor due diligence tracker small business software`
surfaced hosted vendor-risk/audit SaaS products and security-team-oriented
vendor assessment repos. This project is instead a single-file offline CLI
for a small business's purchasing-side record keeping. That is a statement
about what those searches found on that date, not a claim that no similar
CLI exists.

## Install and quick start

Requires Python 3.9+. No dependencies.

```bash
python3 supplier_check.py --help
```

Walkthrough (each command saves automatically to `--db`, default
`supplier_check.json`):

```bash
# register a supplier (GSTIN is optional and format-checked only)
python3 supplier_check.py add-supplier --name "Sharma Wholesale" \
  --contact "9876500000" --gstin 27ABCDE1234F1Z5

# see what the default checklist asks
python3 supplier_check.py checklist

# record what you verified, with dates and notes
python3 supplier_check.py record --supplier s1 --check gstin_format \
  --outcome pass --date 2026-11-10
python3 supplier_check.py record --supplier s1 --check address \
  --outcome pass --date 2026-11-10 --note "visited godown in person"
python3 supplier_check.py record --supplier s1 --check bank \
  --outcome fail --date 2026-11-11 --note "account in personal name"
python3 supplier_check.py status --supplier s1     # verdict: FAILED: bank

# supplier fixed the problem? record a newer pass and the verdict recovers
python3 supplier_check.py record --supplier s1 --check bank \
  --outcome pass --date 2026-11-14 --note "new current account confirmed"
python3 supplier_check.py record --supplier s1 --check terms \
  --outcome pass --date 2026-11-14 --note "30-day credit in writing"
python3 supplier_check.py status --supplier s1     # verdict: READY

python3 supplier_check.py list-suppliers           # everyone, with verdicts
python3 supplier_check.py ready                    # only READY suppliers
python3 supplier_check.py export-csv suppliers.csv # full matrix as CSV
```

Suppliers can be referred to by id (`s1`) or by exact name.

## How verdicts work

- Each supplier gets one status line per checklist item: PASS, FAIL, n/a,
  or `----` (never recorded), from the **latest** dated record (ties on the
  same day go to the later entry).
- `READY` = every **required** check has a latest outcome of pass (or n/a,
  for checks that genuinely do not apply - record the reason in the note).
- `MISSING: ...` = one or more required checks have no pass yet.
- `FAILED: ...` = any check's latest outcome is fail, required or not. This
  beats everything until a newer record passes.
- Optional checks never block READY unless they fail.

## Data and tests

- Store: one JSON file (`--db`, default `supplier_check.json` next to the
  script), written with LF line endings and atomic replace. A failed
  serialization leaves the previous file untouched (tested).
- Tests: 36 unit tests (`python3 -m unittest -v`) covering GSTIN format
  validation, checklist management, record/latest logic, verdict rules
  (missing / ready / fail-beats-ready / recovery), CSV export, persistence,
  and the CLI. CI runs them on ubuntu-24.04 and windows-latest, Python 3.9
  and 3.13.

## License

MIT - see [LICENSE](LICENSE). Copyright (c) 2026 Mehul Jhabak.
