import unittest
import os
from pathlib import Path
from app.db.connector import build_engine
from app.db.demo_db import generate_demo_database
from app.db import schema_reader as sr
from app.db.size_analyzer import get_database_storage_overview, format_bytes
from app.db.maintenance import run_maintenance_action
from app.db.type_optimizer import profile_table_columns
from app.ai.index_advisor import scan_database_indexes, find_redundant_indexes, find_missing_fk_indexes
from app.db.connections import save_last_session, get_last_session


class TestSQLHelperCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = (Path(__file__).parent / "test_sample.db").resolve()
        generate_demo_database(cls.test_db_path, force_recreate=True)
        cls.engine = build_engine("sqlite", sqlite_path=str(cls.test_db_path))

    @classmethod
    def tearDownClass(cls):
        if cls.engine:
            cls.engine.dispose()
        if cls.test_db_path.exists():
            try:
                cls.test_db_path.unlink()
            except Exception:
                pass

    def test_schema_reader_tables(self):
        tables = sr.list_tables(self.engine, "main")
        self.assertIn("customers", tables)
        self.assertIn("orders", tables)
        self.assertIn("order_items", tables)
        self.assertIn("products", tables)
        self.assertIn("activity_logs", tables)

    def test_schema_reader_columns_and_indexes(self):
        cols = sr.get_columns(self.engine, "main", "customers")
        col_names = [c["column"] for c in cols]
        self.assertIn("email", col_names)
        self.assertIn("customer_code", col_names)

        indexes = sr.get_indexes(self.engine, "main", "customers")
        idx_names = [i["name"] for i in indexes]
        self.assertIn("idx_cust_email_1", idx_names)
        self.assertIn("idx_cust_email_2", idx_names)

    def test_size_analyzer(self):
        overview = get_database_storage_overview(self.engine, "main")
        self.assertEqual(overview["dialect"], "sqlite")
        self.assertGreater(overview["total_size_bytes"], 0)
        self.assertGreater(overview["table_count"], 0)
        self.assertGreater(overview["total_rows"], 0)
        self.assertIsInstance(overview["tables"], list)

    def test_maintenance_vacuum(self):
        res = run_maintenance_action(self.engine, "vacuum")
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "vacuum")
        self.assertIn("VACUUM", res["sql_executed"])

    def test_type_optimizer_downcast_and_shrink(self):
        cust_sugg = profile_table_columns(self.engine, "main", "customers", sample_limit=500)
        categories = [s["category"] for s in cust_sugg]
        self.assertTrue(len(cust_sugg) > 0)

    def test_index_advisor_duplicates_and_missing_fks(self):
        findings = scan_database_indexes(self.engine, "main")
        dup_names = [d["index"] for d in findings["duplicates"]]
        self.assertTrue(any("idx_cust_email" in n for n in dup_names))

        missing_fk_cols = [m.get("column") for m in findings["missing_fk"]]
        self.assertTrue("product_id" in missing_fk_cols or "customer_id" in missing_fk_cols)

    def test_session_persistence(self):
        original = get_last_session()
        try:
            save_last_session("test-mock-conn", "test_db", ["t1", "t2"])
            sess = get_last_session()
            self.assertEqual(sess.get("connection_id"), "test-mock-conn")
            self.assertEqual(sess.get("database"), "test_db")
            self.assertEqual(sess.get("tables"), ["t1", "t2"])
        finally:
            if original:
                save_last_session(original.get("connection_id", ""), original.get("database", ""), original.get("tables", []))

    def test_type_optimizer_multi_stage(self):
        from app.db.type_optimizer import profile_table_columns
        # Test profiling on demo sqlite engine
        suggestions = profile_table_columns(self.engine, "main", "customers", sample_limit=1000, deep_verify=True)
        self.assertIsInstance(suggestions, list)

    def test_ai_type_advisor_prompt_and_parser(self):
        from app.ai.type_advisor import build_type_audit_prompt, parse_ai_type_audit
        suggs = [{
            "column": "tracking_num",
            "category": "Integer Downcast",
            "current_type": "VARCHAR(50)",
            "suggested_type": "BIGINT",
            "reason": "Contains numbers",
        }]
        prompt = build_type_audit_prompt("mysql", "orders", suggs)
        self.assertIn("orders", prompt)
        self.assertIn("tracking_num", prompt)

        raw_ai = """COLUMN: tracking_num
STATUS: CAUTION
CONFIDENCE: HIGH
ANALYSIS: Carrier tracking codes may contain alphanumeric prefixes like 1Z. Keep as VARCHAR.
SUMMARY VERDICT: Review tracking_num before applying."""
        parsed = parse_ai_type_audit(raw_ai)
        self.assertIn("tracking_num", parsed)
        self.assertEqual(parsed["tracking_num"]["status"], "CAUTION")


if __name__ == "__main__":
    unittest.main()

