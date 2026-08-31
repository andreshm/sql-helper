import unittest
from pathlib import Path
from app.db.connector import build_engine
from app.db.demo_db import generate_demo_database
from app.ai.validator import verify_sql_against_schema, extract_and_verify_sql_statements


class TestAntiHallucinationValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_path = (Path(__file__).parent / "test_validator.db").resolve()
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

    def test_valid_create_index(self):
        sql = 'CREATE INDEX idx_orders_test ON orders (customer_id, order_date);'
        res = verify_sql_against_schema(self.engine, sql, database="main", target_table="orders")
        self.assertTrue(res["is_valid"])
        self.assertIn("Verified", res["badge"])
        self.assertEqual(res["table"], "orders")
        self.assertEqual(res["columns"], ["customer_id", "order_date"])

    def test_hallucinated_table_name(self):
        sql = 'CREATE INDEX idx_fake ON nonexistent_table (col1);'
        res = verify_sql_against_schema(self.engine, sql, database="main")
        self.assertFalse(res["is_valid"])
        self.assertTrue(any("nonexistent_table" in iss for iss in res["issues"]))

    def test_hallucinated_column_name(self):
        sql = 'CREATE INDEX idx_bad_col ON customers (fake_column_xyz);'
        res = verify_sql_against_schema(self.engine, sql, database="main", target_table="customers")
        self.assertFalse(res["is_valid"])
        self.assertTrue(any("fake_column_xyz" in iss for iss in res["issues"]))

    def test_extract_and_verify_markdown(self):
        markdown_text = """
Here is my analysis:
```sql
CREATE INDEX idx_good ON products (price);
CREATE INDEX idx_bad ON fake_table (xyz);
```
"""
        verified = extract_and_verify_sql_statements(self.engine, markdown_text, database="main")
        self.assertEqual(len(verified), 2)
        self.assertTrue(verified[0]["is_valid"])
        self.assertFalse(verified[1]["is_valid"])

    def test_extract_drop_index_from_bullet_markdown(self):
        markdown_text = """
VERDICT Over-indexed.
INDEXES TO DROP
• DROP INDEX PRIMARY ON customers; Justification: The composite index already covers all queries.
• DROP INDEX idx_cust_email_1 ON customers; Justification: Duplicate of idx_cust_email_2.
• DROP INDEX idx_nonexistent ON customers; Justification: Redundant.

RECOMMENDED INDEXES
```sql
CREATE INDEX idx_orders_combo ON orders (customer_id, order_date);
```
"""
        verified = extract_and_verify_sql_statements(self.engine, markdown_text, database="main", target_table="customers")
        # Should extract: PRIMARY, idx_cust_email_1, idx_nonexistent, and idx_orders_combo
        sqls = [v["sql"] for v in verified]
        self.assertTrue(any("idx_cust_email_1" in s for s in sqls))
        self.assertTrue(any("idx_orders_combo" in s for s in sqls))

        # PRIMARY drop should be flagged with safety protection
        primary_val = next((v for v in verified if "PRIMARY" in v["sql"]), None)
        self.assertIsNotNone(primary_val)
        self.assertFalse(primary_val["is_valid"])
        self.assertTrue(any("PRIMARY KEY" in iss for iss in primary_val["issues"]))

        # idx_cust_email_1 exists on customers, so it should be valid
        email1_val = next((v for v in verified if "idx_cust_email_1" in v["sql"]), None)
        self.assertIsNotNone(email1_val)
        self.assertTrue(email1_val["is_valid"])


if __name__ == "__main__":
    unittest.main()
