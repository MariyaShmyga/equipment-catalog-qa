"""Persistence and database integrity checks, separate from HTTP assertions."""

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from catalog.db import connect, initialize
from catalog.main import create_app


def test_seed_is_repeatable_without_duplicates(client, database_path):
    initialize(database_path, client.app.state.seed_dir)
    with closing(connect(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 48
        assert connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 6


def test_quotes_survive_app_restart(database_path):
    app = create_app(db_path=database_path)
    with TestClient(app) as client:
        response = client.post("/api/quotes", json={
            "name": "Repeat Buyer", "email": "buyer@example.org", "product_id": 1, "quantity": 1
        })
        assert response.status_code == 201
    # Startup should not drop or reseed the submissions table.
    with TestClient(create_app(db_path=database_path)):
        pass
    with closing(connect(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1


def test_foreign_keys_reject_unknown_category(client, db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO products
            (id, sku, name, category_slug, manufacturer, price_rub, stock)
            VALUES (999, 'BAD-1', 'Wrong', 'unknown', 'Test', 100, 1)""")
    db.rollback()


def test_foreign_keys_reject_unknown_product_in_quote(client, db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO quotes (customer_name, email, product_id, quantity)
                      VALUES ('Buyer', 'buyer@example.org', 999999, 1)""")
    db.rollback()


@pytest.mark.parametrize("price,stock", [(0, 3), (-50, 2), (100, -1)])
def test_database_constraints_on_products(client, db, price, stock):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO products
            (id, sku, name, category_slug, manufacturer, price_rub, stock)
            VALUES (999, 'BAD-1', 'Wrong', 'lighting', 'Test', ?, ?)""", (price, stock))
    db.rollback()


def test_quotes_table_has_unique_primary_key(client, db):
    db.execute("""INSERT INTO quotes (id, customer_name, email, product_id, quantity)
                  VALUES (999, 'Buyer', 'buyer@example.org', 1, 1)""")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("""INSERT INTO quotes (id, customer_name, email, product_id, quantity)
                      VALUES (999, 'Other', 'other@example.org', 1, 1)""")
    db.rollback()
