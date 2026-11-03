"""SQLite setup and seed import. Kept separate from HTTP handlers."""

import csv
import json
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category_slug TEXT NOT NULL REFERENCES categories(slug),
    manufacturer TEXT NOT NULL,
    price_rub INTEGER NOT NULL CHECK (price_rub > 0),
    stock INTEGER NOT NULL CHECK (stock >= 0)
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_slug);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price_rub);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 100),
    comment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_quotes_product ON quotes(product_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path,
        timeout=10,
        check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: Path, seed_dir: Path) -> None:
    """Create schema, then import only missing seed rows.

    Repeated server restarts must not erase submitted requests.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as connection:
        connection.executescript(SCHEMA)
        with (seed_dir / "categories.json").open(encoding="utf-8") as file:
            categories = json.load(file)
        with (seed_dir / "products.csv").open(encoding="utf-8", newline="") as file:
            products = list(csv.DictReader(file))
        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO categories (slug, title) VALUES (:slug, :title)",
                categories,
            )
            connection.executemany(
                """INSERT OR IGNORE INTO products
                   (id, sku, name, category_slug, manufacturer, price_rub, stock)
                   VALUES (:id, :sku, :name, :category_slug, :manufacturer, :price_rub, :stock)""",
                products,
            )
