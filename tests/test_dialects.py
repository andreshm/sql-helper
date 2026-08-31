import unittest
from app.ai.index_advisor import (
    find_redundant_indexes,
    find_missing_fk_indexes,
    find_low_cardinality_indexes,
    _drop_index_sql,
    _create_index_sql,
)
from app.db.type_optimizer import _alter_col_sql
from app.db.size_analyzer import format_bytes


class TestDialectsAndLogic(unittest.TestCase):
    def test_format_bytes(self):
        self.assertEqual(format_bytes(500), "500 B")
        self.assertEqual(format_bytes(1024), "1.00 KB")
        self.assertEqual(format_bytes(1048576), "1.00 MB")
        self.assertEqual(format_bytes(1073741824), "1.00 GB")

    def test_index_drop_sql_generation(self):
        # MySQL
        mysql_sql = _drop_index_sql("idx_users_email", "users", "mysql", "mydb")
        self.assertIn("DROP INDEX `idx_users_email` ON `mydb`.`users`", mysql_sql)

        # PostgreSQL
        pg_sql = _drop_index_sql("idx_users_email", "users", "postgresql", "mydb")
        self.assertIn('DROP INDEX CONCURRENTLY IF EXISTS "idx_users_email"', pg_sql)

        # SQLite
        sqlite_sql = _drop_index_sql("idx_users_email", "users", "sqlite", "main")
        self.assertIn('DROP INDEX IF EXISTS "idx_users_email"', sqlite_sql)

    def test_index_create_sql_generation(self):
        # MySQL
        mysql_create = _create_index_sql("idx_orders_user", "orders", ["user_id", "status"], "mysql", "shop")
        self.assertIn("CREATE INDEX `idx_orders_user` ON `shop`.`orders` (`user_id`, `status`)", mysql_create)

        # PostgreSQL
        pg_create = _create_index_sql("idx_orders_user", "orders", ["user_id", "status"], "postgresql", "shop")
        self.assertIn('CREATE INDEX CONCURRENTLY "idx_orders_user" ON "orders" ("user_id", "status")', pg_create)

    def test_type_alter_sql_generation(self):
        # MySQL
        mysql_alter = _alter_col_sql("mysql", "shop", "users", "tier", "TINYINT UNSIGNED", nullable=False)
        self.assertEqual(mysql_alter, "ALTER TABLE `shop`.`users` MODIFY COLUMN `tier` TINYINT UNSIGNED NOT NULL;")

        # PostgreSQL
        pg_alter = _alter_col_sql("postgresql", "shop", "users", "tier", "SMALLINT", nullable=False)
        self.assertIn('ALTER TABLE "users" ALTER COLUMN "tier" TYPE SMALLINT;', pg_alter)
        self.assertIn('ALTER TABLE "users" ALTER COLUMN "tier" SET NOT NULL;', pg_alter)

    def test_prefix_redundant_index_detection(self):
        indexes = [
            {"name": "idx_single", "columns": ["user_id"], "unique": False},
            {"name": "idx_composite", "columns": ["user_id", "order_date", "status"], "unique": False},
        ]
        issues = find_redundant_indexes(indexes, "orders", "mysql", "shop")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "Redundant")
        self.assertEqual(issues[0]["index"], "idx_single")

    def test_missing_fk_detection(self):
        indexes = [
            {"name": "PRIMARY", "columns": ["order_id"], "unique": True},
        ]
        fks = [
            {"column": "customer_id", "ref_table": "customers", "ref_column": "id"},
        ]
        issues = find_missing_fk_indexes(indexes, fks, "orders", "postgresql")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "Missing FK index")
        self.assertEqual(issues[0]["column"], "customer_id")


if __name__ == "__main__":
    unittest.main()
