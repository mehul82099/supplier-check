#!/usr/bin/env python3
"""supplier-check: zero-dependency supplier due-diligence record CLI for small
businesses.

Before buying from a new supplier, a small business should verify a handful of
things: GSTIN format, physical address, bank account in the business's name,
sample quality, trade references, written payment terms. This tool does not
verify any of that for you - it keeps a disciplined record of the checks YOU
performed, so "did we ever confirm their bank account?" has an answer with a
date and a note.

Python 3.9+, standard library only. One JSON file, offline, no accounts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import date

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "supplier_check.json")

OUTCOMES = ("pass", "fail", "na")

# Default verification checklist. `required` checks gate the READY verdict.
# These are record-keeping prompts, not legal or compliance advice.
DEFAULT_CHECKLIST = [
    {"key": "gstin_format", "required": True,
     "label": "GSTIN format checked (15-character format only - not a "
              "government registry lookup)"},
    {"key": "address", "required": True,
     "label": "Physical business address confirmed"},
    {"key": "bank", "required": True,
     "label": "Bank account is in the supplier's business name"},
    {"key": "sample", "required": False,
     "label": "Sample order quality checked"},
    {"key": "references", "required": False,
     "label": "Trade references called"},
    {"key": "terms", "required": True,
     "label": "Payment terms agreed in writing"},
]

# GSTIN: 2 state digits + 10-char PAN pattern + 1 entity code + 'Z' + 1 check
# char. Format check only; it proves nothing about registration.
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


class SupplierCheckError(Exception):
    """User-facing error (bad input, unknown id, ...)."""


def today_iso():
    return date.today().isoformat()


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise SupplierCheckError(
            "bad date %r (expected YYYY-MM-DD)" % text)


def valid_check_key(key):
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key or ""))


def gstin_format_ok(gstin):
    return bool(GSTIN_RE.match(gstin or ""))


class SupplierBook:
    def __init__(self):
        self.state = {"suppliers": [], "records": [],
                      "checklist": [dict(c) for c in DEFAULT_CHECKLIST],
                      "next_supplier_id": 1}

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SupplierCheckError(
                "could not parse %s: %s" % (path, exc))
        book = cls()
        for key in state:
            if key in book.state:
                book.state[key] = state[key]
        return book

    def save(self, path):
        data = json.dumps(self.state, indent=2, ensure_ascii=False) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- checklist --------------------------------------------------------

    def get_check(self, key):
        for c in self.state["checklist"]:
            if c["key"] == key:
                return c
        raise SupplierCheckError(
            "no such check: %s (see `checklist` for keys)" % key)

    def add_check(self, key, label, required):
        key = (key or "").strip()
        label = (label or "").strip()
        if not valid_check_key(key):
            raise SupplierCheckError(
                "check key must be lowercase letters/digits/underscore, "
                "starting with a letter: %r" % key)
        if not label:
            raise SupplierCheckError("check label must not be empty")
        if any(c["key"] == key for c in self.state["checklist"]):
            raise SupplierCheckError("check already exists: %s" % key)
        self.state["checklist"].append(
            {"key": key, "label": label, "required": bool(required)})
        return self.state["checklist"][-1]

    # -- suppliers --------------------------------------------------------

    def add_supplier(self, name, contact="", gstin="", notes=""):
        name = (name or "").strip()
        if not name:
            raise SupplierCheckError("supplier name must not be empty")
        if any(s["name"].lower() == name.lower()
               for s in self.state["suppliers"]):
            raise SupplierCheckError("supplier already exists: %s" % name)
        gstin = (gstin or "").strip().upper()
        if gstin and not gstin_format_ok(gstin):
            raise SupplierCheckError(
                "GSTIN fails the 15-character format check: %s" % gstin)
        sup = {"id": "s%d" % self.state["next_supplier_id"], "name": name,
               "contact": (contact or "").strip(), "gstin": gstin,
               "notes": (notes or "").strip(), "added": today_iso()}
        self.state["next_supplier_id"] += 1
        self.state["suppliers"].append(sup)
        return sup

    def get_supplier(self, ref):
        ref = (ref or "").strip()
        for s in self.state["suppliers"]:
            if s["id"] == ref or s["name"].lower() == ref.lower():
                return s
        raise SupplierCheckError("no such supplier: %s" % ref)

    # -- records ----------------------------------------------------------

    def record(self, supplier_id, check_key, outcome, when, note=""):
        sup = self.get_supplier(supplier_id)
        check = self.get_check(check_key)
        if outcome not in OUTCOMES:
            raise SupplierCheckError(
                "outcome must be one of: %s" % ", ".join(OUTCOMES))
        rec = {"supplier_id": sup["id"], "check": check["key"],
               "outcome": outcome, "date": when.isoformat(),
               "note": (note or "").strip()}
        self.state["records"].append(rec)
        return rec

    def latest_records(self, supplier_id):
        """check key -> latest record (ties: later entry wins)."""
        latest = {}
        for r in self.state["records"]:
            if r["supplier_id"] != supplier_id:
                continue
            cur = latest.get(r["check"])
            if cur is None or r["date"] >= cur["date"]:
                latest[r["check"]] = r
        return latest

    # -- verdict ----------------------------------------------------------

    def supplier_status(self, supplier_id):
        sup = self.get_supplier(supplier_id)
        latest = self.latest_records(sup["id"])
        lines = []
        missing_required, failed = [], []
        for c in self.state["checklist"]:
            rec = latest.get(c["key"])
            outcome = rec["outcome"] if rec else "not done"
            if rec and rec["outcome"] == "fail":
                failed.append(c["key"])
            if c["required"] and (rec is None or rec["outcome"] not in
                                  ("pass", "na")):
                missing_required.append(c["key"])
            lines.append({"key": c["key"], "label": c["label"],
                          "required": c["required"], "outcome": outcome,
                          "date": rec["date"] if rec else "",
                          "note": rec["note"] if rec else ""})
        if failed:
            verdict = "FAILED: " + ", ".join(failed)
        elif missing_required:
            verdict = "MISSING: " + ", ".join(missing_required)
        else:
            verdict = "READY"
        return {"supplier": sup, "checks": lines, "verdict": verdict}

    def ready_suppliers(self):
        return [s for s in self.state["suppliers"]
                if self.supplier_status(s["id"])["verdict"] == "READY"]

    def export_csv(self, out_path):
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["supplier_id", "supplier", "gstin", "check",
                        "required", "outcome", "date", "verdict"])
            for s in self.state["suppliers"]:
                st = self.supplier_status(s["id"])
                for line in st["checks"]:
                    w.writerow([s["id"], s["name"], s["gstin"], line["key"],
                                "yes" if line["required"] else "no",
                                line["outcome"], line["date"], st["verdict"]])

    def summary(self):
        ready = len(self.ready_suppliers())
        return {"suppliers": len(self.state["suppliers"]),
                "records": len(self.state["records"]),
                "checks": len(self.state["checklist"]),
                "ready": ready}


# -- CLI --------------------------------------------------------------------

def cmd_add_supplier(book, args):
    sup = book.add_supplier(args.name, args.contact, args.gstin, args.notes)
    print("added supplier %s: %s" % (sup["id"], sup["name"]))
    if sup["gstin"]:
        print("  GSTIN recorded (format check only - not registry-verified)")


def cmd_list_suppliers(book, args):
    if not book.state["suppliers"]:
        print("no suppliers yet")
        return
    for s in book.state["suppliers"]:
        st = book.supplier_status(s["id"])
        print("%-4s  %-28s  %s" % (s["id"], s["name"], st["verdict"]))


def cmd_checklist(book, args):
    for c in book.state["checklist"]:
        gate = "required" if c["required"] else "optional"
        print("%-14s  %-9s  %s" % (c["key"], gate, c["label"]))


def cmd_add_check(book, args):
    c = book.add_check(args.key, args.label, args.required)
    print("added check %s (%s): %s"
          % (c["key"], "required" if c["required"] else "optional",
             c["label"]))


def cmd_record(book, args):
    rec = book.record(args.supplier, args.check, args.outcome,
                      parse_date(args.date) if args.date else date.today(),
                      args.note)
    sup = book.get_supplier(args.supplier)
    print("recorded: %s / %s = %s on %s"
          % (sup["name"], rec["check"], rec["outcome"], rec["date"]))


def _print_status(st):
    sup = st["supplier"]
    print("%s (%s)%s" % (sup["name"], sup["id"],
                         "  GSTIN " + sup["gstin"] if sup["gstin"] else ""))
    for line in st["checks"]:
        mark = {"pass": "PASS", "fail": "FAIL", "na": "n/a"}.get(
            line["outcome"], "----")
        req = "*" if line["required"] else " "
        extra = ("  %s" % line["date"]) if line["date"] else ""
        note = ("  - %s" % line["note"]) if line["note"] else ""
        print("  %s %-14s %s%s%s" % (req, line["key"], mark, extra, note))
    print("  verdict: %s   (* = required check)" % st["verdict"])


def cmd_status(book, args):
    if args.supplier:
        _print_status(book.supplier_status(args.supplier))
        return
    if not book.state["suppliers"]:
        print("no suppliers yet")
        return
    for s in book.state["suppliers"]:
        _print_status(book.supplier_status(s["id"]))
        print()


def cmd_ready(book, args):
    ready = book.ready_suppliers()
    if not ready:
        print("no suppliers are READY yet")
        return
    for s in ready:
        print("%-4s  %s" % (s["id"], s["name"]))


def cmd_export_csv(book, args):
    book.export_csv(args.path)
    print("wrote %s" % args.path)


def cmd_summary(book, args):
    s = book.summary()
    print("suppliers: %d   records: %d   checks: %d   ready: %d"
          % (s["suppliers"], s["records"], s["checks"], s["ready"]))


def build_parser():
    p = argparse.ArgumentParser(
        prog="supplier-check",
        description="Supplier due-diligence record book for small "
                    "businesses. Records checks YOU performed; verifies "
                    "nothing itself.")
    p.add_argument("--db", default=DEFAULT_DB,
                   help="store file (default: supplier_check.json next to "
                        "this script)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("add-supplier", help="add a supplier")
    sp.add_argument("--name", required=True)
    sp.add_argument("--contact", default="")
    sp.add_argument("--gstin", default="",
                    help="15-char GSTIN (format check only)")
    sp.add_argument("--notes", default="")
    sp.set_defaults(func=cmd_add_supplier)

    sp = sub.add_parser("list-suppliers", help="list suppliers with verdicts")
    sp.set_defaults(func=cmd_list_suppliers)

    sp = sub.add_parser("checklist", help="show the verification checklist")
    sp.set_defaults(func=cmd_checklist)

    sp = sub.add_parser("add-check", help="add a custom checklist item")
    sp.add_argument("--key", required=True,
                    help="lowercase key, e.g. warehouse_visit")
    sp.add_argument("--label", required=True)
    sp.add_argument("--required", action="store_true",
                    help="gate the READY verdict on this check")
    sp.set_defaults(func=cmd_add_check)

    sp = sub.add_parser("record", help="record a check outcome")
    sp.add_argument("--supplier", required=True, help="id or exact name")
    sp.add_argument("--check", required=True, help="checklist key")
    sp.add_argument("--outcome", required=True, choices=OUTCOMES)
    sp.add_argument("--date", default=None,
                    help="YYYY-MM-DD (default: today)")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("status", help="show per-check status and verdict")
    sp.add_argument("--supplier", default=None, help="id or exact name")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("ready", help="list READY suppliers")
    sp.set_defaults(func=cmd_ready)

    sp = sub.add_parser("export-csv", help="export the full matrix to CSV")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_export_csv)

    sp = sub.add_parser("summary", help="one-line store summary")
    sp.set_defaults(func=cmd_summary)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    from pathlib import Path
    db = Path(args.db)
    try:
        book = SupplierBook.load(db)
        args.func(book, args)
        book.save(db)
    except SupplierCheckError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
