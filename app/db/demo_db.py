"""
Demo database generator.
Creates a realistic SQLite database with intentional optimization opportunities:
  - Inefficient data types (oversized VARCHAR, BIGINT for tiny integers, FLOAT for money)
  - Duplicate and redundant indexes
  - Missing foreign key indexes
  - Deleted row fragmentation / bloat for vacuuming demonstration
"""
from __future__ import annotations
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DEMO_DB_PATH = (Path(__file__).parent.parent.parent / "demo_ecommerce.db").resolve()


def generate_demo_database(db_path: Path | str | None = None, force_recreate: bool = False) -> str:
    path = Path(db_path) if db_path else DEMO_DB_PATH
    if path.exists() and not force_recreate:
        return str(path)

    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass

    conn = sqlite3.connect(str(path))
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Customers Table
    cursor.execute(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_code VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL,
            phone VARCHAR(100),
            is_active INTEGER DEFAULT 1,
            loyalty_tier BIGINT DEFAULT 1,
            created_at DATETIME NOT NULL
        );
        """
    )
    # Duplicate & redundant indexes on customers
    cursor.execute("CREATE INDEX idx_cust_email_1 ON customers (email);")
    cursor.execute("CREATE INDEX idx_cust_email_2 ON customers (email);")  # Exact duplicate
    cursor.execute("CREATE INDEX idx_cust_active ON customers (is_active);")  # Low cardinality boolean

    # 2. Products Table
    cursor.execute(
        """
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            title VARCHAR(255) NOT NULL,
            category_id BIGINT NOT NULL,
            price FLOAT NOT NULL,
            stock_qty BIGINT NOT NULL,
            is_available INTEGER DEFAULT 1,
            description TEXT
        );
        """
    )
    cursor.execute("CREATE UNIQUE INDEX idx_prod_sku ON products (sku);")
    cursor.execute("CREATE INDEX idx_prod_cat ON products (category_id);")

    # 3. Orders Table
    cursor.execute(
        """
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            order_date DATETIME NOT NULL,
            status VARCHAR(100) NOT NULL,
            total_amount FLOAT NOT NULL,
            shipping_zip VARCHAR(255),
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        );
        """
    )
    # Prefix redundant indexes on orders: (status) is a prefix of (status, order_date)
    cursor.execute("CREATE INDEX idx_orders_status ON orders (status);")
    cursor.execute("CREATE INDEX idx_orders_status_date ON orders (status, order_date);")
    # Note: customer_id is a Foreign Key but has NO index!

    # 4. Order Items Table
    cursor.execute(
        """
        CREATE TABLE order_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity BIGINT NOT NULL,
            unit_price FLOAT NOT NULL,
            discount_code TEXT,
            FOREIGN KEY (order_id) REFERENCES orders (order_id),
            FOREIGN KEY (product_id) REFERENCES products (product_id)
        );
        """
    )
    # order_id has an index, but product_id does NOT (Missing FK Index)
    cursor.execute("CREATE INDEX idx_items_order ON order_items (order_id);")

    # 5. Activity Logs Table (for fragmentation & bloat demonstration)
    cursor.execute(
        """
        CREATE TABLE activity_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_ip VARCHAR(255),
            endpoint VARCHAR(255),
            payload TEXT,
            status_code BIGINT,
            logged_at DATETIME
        );
        """
    )
    cursor.execute("CREATE INDEX idx_logs_logged ON activity_logs (logged_at);")

    # -------------------------------------------------------------
    # Populate Seed Data
    # -------------------------------------------------------------
    random.seed(42)

    # Customers (200 records)
    first_names = ["Emma", "Liam", "Olivia", "Noah", "Sophia", "Jackson", "Ava", "Lucas", "Mia", "Oliver"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]
    statuses = ["PENDING", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"]

    cust_rows = []
    base_time = datetime(2025, 1, 1, 10, 0, 0)
    for i in range(1, 201):
        c_code = f"CUST{i:04d}"
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        name = f"{fn} {ln}"
        email = f"{fn.lower()}.{ln.lower()}{i}@example.com"
        phone = f"+1-555-{random.randint(100,999)}-{random.randint(1000,9999)}"
        is_act = 1 if random.random() > 0.1 else 0
        loyalty = random.randint(1, 4)
        c_date = base_time + timedelta(days=random.randint(0, 365), hours=random.randint(0, 23))
        cust_rows.append((c_code, name, email, phone, is_act, loyalty, c_date.strftime("%Y-%m-%d %H:%M:%S")))

    cursor.executemany(
        "INSERT INTO customers (customer_code, name, email, phone, is_active, loyalty_tier, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        cust_rows,
    )

    # Products (50 records)
    categories = ["Electronics", "Books", "Apparel", "Home", "Sports"]
    prod_rows = []
    for i in range(1, 51):
        sku = f"SKU-{i:03d}"
        cat_id = random.randint(1, len(categories))
        title = f"{categories[cat_id - 1]} Item {i}"
        price = round(random.uniform(9.99, 499.99), 2)
        stock = random.randint(5, 500)
        desc = f"High quality {title} with standard warranty and fast fulfillment."
        prod_rows.append((sku, title, cat_id, price, stock, 1, desc))

    cursor.executemany(
        "INSERT INTO products (sku, title, category_id, price, stock_qty, is_available, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
        prod_rows,
    )

    # Orders (800 records)
    order_rows = []
    for i in range(1, 801):
        c_id = random.randint(1, 200)
        o_date = base_time + timedelta(days=random.randint(1, 365), hours=random.randint(0, 23))
        st = random.choice(statuses)
        total = round(random.uniform(15.0, 1200.0), 2)
        zip_code = f"{random.randint(10001, 99950)}"
        order_rows.append((c_id, o_date.strftime("%Y-%m-%d %H:%M:%S"), st, total, zip_code))

    cursor.executemany(
        "INSERT INTO orders (customer_id, order_date, status, total_amount, shipping_zip) VALUES (?, ?, ?, ?, ?)",
        order_rows,
    )

    # Order Items (2,000 records)
    item_rows = []
    for i in range(1, 2001):
        o_id = random.randint(1, 800)
        p_id = random.randint(1, 50)
        qty = random.randint(1, 5)
        u_price = round(random.uniform(9.99, 299.99), 2)
        d_code = "SAVE10" if random.random() < 0.2 else None
        item_rows.append((o_id, p_id, qty, u_price, d_code))

    cursor.executemany(
        "INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_code) VALUES (?, ?, ?, ?, ?)",
        item_rows,
    )

    # Activity Logs: Insert 4,000 rows, then DELETE 3,200 rows to create deliberate table bloat and freelist pages
    log_rows = []
    for i in range(1, 4001):
        ip = f"192.168.{random.randint(1,254)}.{random.randint(1,254)}"
        ep = random.choice(["/api/v1/checkout", "/api/v1/products", "/api/v1/user/profile", "/api/v1/search"])
        payload = f'{{"request_id": "{i:06d}", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "meta": "{x_str(random.randint(50, 150))}"}}'
        status = random.choice([200, 200, 200, 201, 400, 404, 500])
        l_date = base_time + timedelta(minutes=i * 2)
        log_rows.append((ip, ep, payload, status, l_date.strftime("%Y-%m-%d %H:%M:%S")))

    cursor.executemany(
        "INSERT INTO activity_logs (user_ip, endpoint, payload, status_code, logged_at) VALUES (?, ?, ?, ?, ?)",
        log_rows,
    )
    conn.commit()

    # Create fragmentation: delete 80% of log rows
    cursor.execute("DELETE FROM activity_logs WHERE log_id % 5 != 0;")
    conn.commit()
    conn.close()

    return str(path)


def x_str(length: int) -> str:
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choice(chars) for _ in range(length))
