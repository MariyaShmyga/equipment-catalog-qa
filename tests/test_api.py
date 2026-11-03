"""HTTP behavior, including invalid requests and boundary values."""

import json
from pathlib import Path

import pytest

BAD_QUOTES = json.loads((Path(__file__).parent / "data" / "invalid_quotes.json").read_text(encoding="utf-8"))


def valid_quote(**updates):
    payload = {"name": "Maria Buyer", "email": "buyer@example.org", "product_id": 1, "quantity": 2}
    payload.update(updates)
    return payload


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_categories_come_from_seed(client):
    response = client.get("/api/categories")
    assert response.status_code == 200
    assert {x["slug"] for x in response.json()["items"]} == {
        "lighting", "audio", "video", "network", "compute", "peripherals"
    }


def test_catalog_has_48_records_from_csv(client):
    response = client.get("/api/products", params={"limit": 50})
    data = response.json()
    assert response.status_code == 200
    assert data["total"] == 48
    assert len(data["items"]) == 48
    assert len({p["sku"] for p in data["items"]}) == 48
    assert all({"id", "sku", "name", "category", "manufacturer", "price_rub", "stock"} <= p.keys()
               for p in data["items"])


@pytest.mark.parametrize("slug", ["lighting", "audio", "video", "network", "compute", "peripherals"])
def test_category_filter(client, slug):
    data = client.get("/api/products", params={"category": slug, "limit": 50}).json()
    assert data["total"] == 8
    assert all(p["category"] == slug for p in data["items"])


@pytest.mark.parametrize("q,expected", [
    ("projector", 1), ("PROJECTOR", 1), ("  projector ", 1),
    ("Monitor", 2), ("not-a-product", 0), ("%", 0), ("_", 0),
])
def test_search(client, q, expected):
    result = client.get("/api/products", params={"q": q}).json()
    assert result["total"] == expected


def test_search_combines_with_category(client):
    data = client.get("/api/products", params={"q": "monitor", "category": "peripherals"}).json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Monitor 27 QHD"


def test_search_does_not_execute_sql(client):
    response = client.get("/api/products", params={"q": "' OR 1=1 --"})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert client.get("/api/products", params={"limit": 50}).json()["total"] == 48


@pytest.mark.parametrize("params,total", [
    ({"in_stock": "true"}, 42), ({"in_stock": "false"}, 6),
    ({"min_price": 200000}, 4), ({"max_price": 6000}, 2),
])
def test_stock_and_price_filters(client, params, total):
    assert client.get("/api/products", params=params).json()["total"] == total


def test_price_bounds_inclusive(client):
    data = client.get("/api/products", params={"min_price": 17900, "max_price": 17900}).json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "LED Fresnel 150"


def test_invalid_price_range(client):
    response = client.get("/api/products", params={"min_price": 50000, "max_price": 2000})
    assert response.status_code == 400
    assert response.json()["detail"] == "min_price cannot exceed max_price"


@pytest.mark.parametrize("sort,reverse", [("price_asc", False), ("price_desc", True)])
def test_price_sort(client, sort, reverse):
    data = client.get("/api/products", params={"sort": sort, "limit": 50}).json()
    prices = [item["price_rub"] for item in data["items"]]
    assert prices == sorted(prices, reverse=reverse)


def test_default_sort_by_name(client):
    names = [p["name"] for p in client.get("/api/products", params={"limit": 50}).json()["items"]]
    assert names == sorted(names, key=str.lower)


def test_pagination_has_no_overlaps_or_gaps(client):
    all_ids = [p["id"] for p in client.get("/api/products", params={"limit": 50}).json()["items"]]
    ids = []
    for offset in (0, 12, 24, 36):
        page = client.get("/api/products", params={"limit": 12, "offset": offset}).json()
        assert page["total"] == 48
        assert len(page["items"]) == 12
        ids.extend([p["id"] for p in page["items"]])
    assert ids == all_ids
    assert len(set(ids)) == 48


def test_offset_beyond_last_page(client):
    data = client.get("/api/products", params={"offset": 200}).json()
    assert data["total"] == 48
    assert data["items"] == []


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 51}, {"offset": -1}, {"min_price": -1},
    {"max_price": -1}, {"sort": "DELETE FROM products"}, {"q": "x" * 81},
    {"in_stock": "perhaps"},
])
def test_invalid_catalog_params(client, params):
    assert client.get("/api/products", params=params).status_code == 422


@pytest.mark.parametrize("product_id", [1, 8, 16, 24, 32, 40, 48])
def test_product_details(client, product_id):
    response = client.get(f"/api/products/{product_id}")
    assert response.status_code == 200
    assert response.json()["id"] == product_id
    assert response.json()["sku"]


@pytest.mark.parametrize("product_id", [0, 99999, -1])
def test_product_not_found(client, product_id):
    assert client.get(f"/api/products/{product_id}").status_code == 404


def test_product_id_must_be_integer(client):
    assert client.get("/api/products/abc").status_code == 422


def test_quote_inserted_in_database(client, db):
    response = client.post("/api/quotes", json=valid_quote(comment="Need delivery date"))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "new"
    saved = db.execute("SELECT * FROM quotes WHERE id = ?", (body["id"],)).fetchone()
    assert saved is not None
    assert saved["customer_name"] == "Maria Buyer"
    assert saved["email"] == "buyer@example.org"
    assert saved["quantity"] == 2
    assert saved["comment"] == "Need delivery date"
    assert saved["product_id"] == 1
    assert saved["created_at"]


def test_two_submissions_get_distinct_ids(client, db):
    first = client.post("/api/quotes", json=valid_quote()).json()["id"]
    second = client.post("/api/quotes", json=valid_quote(name="Alex Buyer")).json()["id"]
    assert second > first
    assert db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 2


@pytest.mark.parametrize("record", BAD_QUOTES, ids=lambda item: item["name"])
def test_invalid_quotes_are_rejected_without_db_writes(client, db, record):
    before = db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    response = client.post("/api/quotes", json=record["body"])
    assert response.status_code == 422
    assert db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == before


@pytest.mark.parametrize("name,should_pass", [
    ("AB", True), ("A" * 60, True), ("A" * 61, False), ("  AB  ", True)
])
def test_name_length_boundaries(client, db, name, should_pass):
    response = client.post("/api/quotes", json=valid_quote(name=name))
    assert response.status_code == (201 if should_pass else 422)
    if should_pass:
        stored = db.execute("SELECT customer_name FROM quotes WHERE id = ?", (response.json()["id"],)).fetchone()
        assert stored["customer_name"] == name.strip()


@pytest.mark.parametrize("size,status", [(0, 201), (500, 201), (501, 422)])
def test_comment_length_boundaries(client, size, status):
    response = client.post("/api/quotes", json=valid_quote(comment="a" * size))
    assert response.status_code == status


@pytest.mark.parametrize("product_id,quantity,status", [
    (1, 1, 201), (1, 10, 201), (1, 11, 409),
    (8, 1, 409), (9999, 1, 404),
])
def test_stock_and_missing_product(client, db, product_id, quantity, status):
    response = client.post("/api/quotes", json=valid_quote(product_id=product_id, quantity=quantity))
    assert response.status_code == status
    assert db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == (1 if status == 201 else 0)


def test_quote_does_not_reduce_stock(client, db):
    """Request for quotation isn't a purchase or a reservation."""
    stock_before = db.execute("SELECT stock FROM products WHERE id=1").fetchone()[0]
    assert client.post("/api/quotes", json=valid_quote()).status_code == 201
    assert db.execute("SELECT stock FROM products WHERE id=1").fetchone()[0] == stock_before
