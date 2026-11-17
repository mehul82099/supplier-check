#!/usr/bin/env python3
"""Tests for supplier_check. Run: python -m unittest -v"""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
from pathlib import Path

import supplier_check as sc

GOOD_GSTIN = "27ABCDE1234F1Z5"   # format-valid sample, not a real registration
BAD_GSTIN = "ABCDE1234F"


class FormatTests(unittest.TestCase):
    def test_gstin_format(self):
        self.assertTrue(sc.gstin_format_ok(GOOD_GSTIN))
        self.assertTrue(sc.gstin_format_ok("09AADCH1234E1ZP"))

    def test_gstin_format_rejects(self):
        for bad in ("", BAD_GSTIN, "27abcde1234f1z5", "27ABCDE1234F1Z",
                    "27ABCDE1234F1Z55", "27ABCD12345F1Z5"):
            self.assertFalse(sc.gstin_format_ok(bad))

    def test_check_key_validation(self):
        self.assertTrue(sc.valid_check_key("warehouse_visit"))
        self.assertFalse(sc.valid_check_key("Warehouse"))
        self.assertFalse(sc.valid_check_key("1st"))
        self.assertFalse(sc.valid_check_key("has space"))
        self.assertFalse(sc.valid_check_key(""))

    def test_bad_date_rejected(self):
        with self.assertRaises(sc.SupplierCheckError):
            sc.parse_date("17/11/2026")


class BookFixture(unittest.TestCase):
    def setUp(self):
        self.book = sc.SupplierBook()
        self.book.add_supplier("Sharma Wholesale", contact="9876500000",
                               gstin=GOOD_GSTIN)


class SupplierTests(BookFixture):
    def test_add_and_lookup(self):
        s = self.book.get_supplier("sharma wholesale")
        self.assertEqual(s["id"], "s1")
        self.assertEqual(s["gstin"], GOOD_GSTIN)

    def test_duplicate_name_rejected(self):
        with self.assertRaises(sc.SupplierCheckError):
            self.book.add_supplier("SHARMA WHOLESALE")

    def test_empty_name_rejected(self):
        with self.assertRaises(sc.SupplierCheckError):
            self.book.add_supplier("  ")

    def test_bad_gstin_rejected(self):
        with self.assertRaises(sc.SupplierCheckError):
            self.book.add_supplier("New Supplier", gstin=BAD_GSTIN)

    def test_gstin_uppercased(self):
        b = sc.SupplierBook()
        s = b.add_supplier("X", gstin=GOOD_GSTIN.lower())
        self.assertEqual(s["gstin"], GOOD_GSTIN)

    def test_unknown_supplier(self):
        with self.assertRaises(sc.SupplierCheckError):
            self.book.get_supplier("s99")


class ChecklistTests(BookFixture):
    def test_default_checklist_has_required_and_optional(self):
        req = [c["key"] for c in self.book.state["checklist"] if c["required"]]
        opt = [c["key"] for c in self.book.state["checklist"]
               if not c["required"]]
        self.assertEqual(req, ["gstin_format", "address", "bank", "terms"])
        self.assertEqual(opt, ["sample", "references"])

    def test_add_custom_check(self):
        c = self.book.add_check("warehouse_visit", "Visited warehouse", True)
        self.assertIn(c, self.book.state["checklist"])

    def test_add_check_rejects_dup_and_bad_key(self):
        with self.assertRaises(sc.SupplierCheckError):
            self.book.add_check("bank", "dup", False)
        with self.assertRaises(sc.SupplierCheckError):
            self.book.add_check("Bad Key", "x", False)
        with self.assertRaises(sc.SupplierCheckError):
            self.book.add_check("ok_key", "  ", False)

    def test_unknown_check(self):
        with self.assertRaises(sc.SupplierCheckError):
            self.book.get_check("nope")


class RecordTests(BookFixture):
    def test_record_and_latest(self):
        self.book.record("s1", "bank", "fail", date(2026, 11, 1), "wrong name")
        self.book.record("s1", "bank", "pass", date(2026, 11, 10))
        latest = self.book.latest_records("s1")
        self.assertEqual(latest["bank"]["outcome"], "pass")
        self.assertEqual(latest["bank"]["date"], "2026-11-10")

    def test_same_day_later_entry_wins(self):
        self.book.record("s1", "bank", "fail", date(2026, 11, 10))
        self.book.record("s1", "bank", "pass", date(2026, 11, 10))
        self.assertEqual(self.book.latest_records("s1")["bank"]["outcome"],
                         "pass")

    def test_bad_outcome_rejected(self):
        with self.assertRaises(sc.SupplierCheckError):
            self.book.record("s1", "bank", "maybe", date(2026, 11, 1))

    def test_record_by_name(self):
        rec = self.book.record("Sharma Wholesale", "address", "pass",
                               date(2026, 11, 2))
        self.assertEqual(rec["supplier_id"], "s1")


class VerdictTests(BookFixture):
    def _pass_required(self):
        for key in ("gstin_format", "address", "bank", "terms"):
            self.book.record("s1", key, "pass", date(2026, 11, 1))

    def test_missing_required(self):
        self.book.record("s1", "bank", "pass", date(2026, 11, 1))
        st = self.book.supplier_status("s1")
        self.assertTrue(st["verdict"].startswith("MISSING:"))
        self.assertIn("gstin_format", st["verdict"])

    def test_ready(self):
        self._pass_required()
        st = self.book.supplier_status("s1")
        self.assertEqual(st["verdict"], "READY")
        self.assertEqual([s["id"] for s in self.book.ready_suppliers()],
                         ["s1"])

    def test_na_counts_as_done_for_required(self):
        for key in ("gstin_format", "address", "bank"):
            self.book.record("s1", key, "pass", date(2026, 11, 1))
        self.book.record("s1", "terms", "na", date(2026, 11, 1),
                         "cash-only supplier, no credit terms needed")
        self.assertEqual(self.book.supplier_status("s1")["verdict"], "READY")

    def test_fail_beats_ready(self):
        self._pass_required()
        self.book.record("s1", "bank", "fail", date(2026, 11, 5),
                         "account in personal name")
        st = self.book.supplier_status("s1")
        self.assertTrue(st["verdict"].startswith("FAILED:"))
        self.assertIn("bank", st["verdict"])
        self.assertEqual(self.book.ready_suppliers(), [])

    def test_optional_missing_still_ready(self):
        self._pass_required()
        # sample + references never done: still READY
        self.assertEqual(self.book.supplier_status("s1")["verdict"], "READY")

    def test_optional_fail_blocks(self):
        self._pass_required()
        self.book.record("s1", "sample", "fail", date(2026, 11, 3))
        self.assertTrue(self.book.supplier_status("s1")["verdict"]
                        .startswith("FAILED:"))

    def test_custom_required_check_gates(self):
        self._pass_required()
        self.book.add_check("warehouse_visit", "Visited warehouse", True)
        st = self.book.supplier_status("s1")
        self.assertIn("warehouse_visit", st["verdict"])
        self.book.record("s1", "warehouse_visit", "pass", date(2026, 11, 4))
        self.assertEqual(self.book.supplier_status("s1")["verdict"], "READY")

    def test_recovered_fail_after_new_pass(self):
        self._pass_required()
        self.book.record("s1", "bank", "fail", date(2026, 11, 5))
        self.book.record("s1", "bank", "pass", date(2026, 11, 9),
                         "new account confirmed")
        self.assertEqual(self.book.supplier_status("s1")["verdict"], "READY")


class CsvTests(BookFixture):
    def test_export(self):
        self.book.record("s1", "bank", "pass", date(2026, 11, 1))
        self.book.add_supplier("Gupta Traders")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "suppliers.csv"
            self.book.export_csv(out)
            with open(out, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["supplier_id", "supplier", "gstin",
                                   "check", "required", "outcome", "date",
                                   "verdict"])
        # 2 suppliers x 6 checks
        self.assertEqual(len(rows), 1 + 12)
        bank_row = [r for r in rows if r[0] == "s1" and r[3] == "bank"][0]
        self.assertEqual(bank_row[5], "pass")
        self.assertEqual(bank_row[6], "2026-11-01")
        self.assertTrue(bank_row[7].startswith("MISSING:"))


class PersistenceTests(BookFixture):
    def test_round_trip_and_lf_only(self):
        self.book.record("s1", "bank", "pass", date(2026, 11, 1))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            self.assertNotIn(b"\r\n", db.read_bytes())
            loaded = sc.SupplierBook.load(db)
        self.assertEqual(loaded.summary(), self.book.summary())
        self.assertEqual(loaded.latest_records("s1")["bank"]["outcome"],
                         "pass")

    def test_failed_save_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            before = db.read_bytes()
            self.book.state["broken"] = object()
            try:
                with self.assertRaises(TypeError):
                    self.book.save(db)
                self.assertEqual(db.read_bytes(), before)
                leftovers = [p for p in Path(tmp).iterdir()
                             if p.name != "book.json"]
                self.assertEqual(leftovers, [])
            finally:
                del self.book.state["broken"]

    def test_corrupt_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bad.json"
            db.write_text("{oops", encoding="utf-8")
            with self.assertRaises(sc.SupplierCheckError):
                sc.SupplierBook.load(db)

    def test_missing_file_gives_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = sc.SupplierBook.load(Path(tmp) / "none.json")
        self.assertEqual(loaded.summary()["suppliers"], 0)
        # defaults still present after load-merge
        self.assertTrue(loaded.state["checklist"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "cli.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sc.main(["--db", self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_flow(self):
        code, out, _ = self.run_cli("add-supplier", "--name", "Sharma Wholesale",
                                    "--contact", "9876500000",
                                    "--gstin", GOOD_GSTIN)
        self.assertEqual(code, 0)
        self.assertIn("s1", out)
        self.assertIn("format check only", out)
        for key in ("gstin_format", "address", "bank", "terms"):
            code, _, _ = self.run_cli("record", "--supplier", "s1",
                                      "--check", key, "--outcome", "pass",
                                      "--date", "2026-11-10")
            self.assertEqual(code, 0)
        code, out, _ = self.run_cli("status", "--supplier", "s1")
        self.assertIn("verdict: READY", out)
        code, out, _ = self.run_cli("ready")
        self.assertIn("Sharma Wholesale", out)
        code, out, _ = self.run_cli("list-suppliers")
        self.assertIn("READY", out)
        code, out, _ = self.run_cli("summary")
        self.assertIn("ready: 1", out)
        out_path = str(Path(self.tmp.name) / "out.csv")
        code, _, _ = self.run_cli("export-csv", out_path)
        self.assertEqual(code, 0)
        with open(out_path, newline="", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.reader(f))), 7)

    def test_checklist_command(self):
        code, out, _ = self.run_cli("checklist")
        self.assertEqual(code, 0)
        self.assertIn("gstin_format", out)
        self.assertIn("required", out)

    def test_custom_check_flow(self):
        self.run_cli("add-supplier", "--name", "A Supplier")
        code, out, _ = self.run_cli("add-check", "--key", "warehouse_visit",
                                    "--label", "Visited warehouse",
                                    "--required")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("status", "--supplier", "s1")
        self.assertIn("warehouse_visit", out)

    def test_error_exit_code(self):
        code, _, err = self.run_cli("record", "--supplier", "s99",
                                    "--check", "bank", "--outcome", "pass")
        self.assertEqual(code, 2)
        self.assertIn("no such supplier", err)
        code, _, err = self.run_cli("add-supplier", "--name", "X",
                                    "--gstin", "bad")
        self.assertEqual(code, 2)
        self.assertIn("format check", err)

    def test_fail_verdict_in_list(self):
        self.run_cli("add-supplier", "--name", "Bad Co")
        self.run_cli("record", "--supplier", "s1", "--check", "bank",
                     "--outcome", "fail", "--date", "2026-11-01")
        code, out, _ = self.run_cli("list-suppliers")
        self.assertIn("FAILED: bank", out)
        code, out, _ = self.run_cli("ready")
        self.assertIn("no suppliers are READY", out)


if __name__ == "__main__":
    unittest.main()
