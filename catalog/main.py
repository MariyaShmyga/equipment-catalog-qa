"""A small procurement catalog for practicing API and browser testing."""

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from catalog.db import connect, initialize

ROOT = Path(__file__).resolve().parent.parent
router = APIRouter(prefix="/api")


class QuoteIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    email: EmailStr
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=100)
    comment: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def non_blank_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Name must contain at least two characters")
        return value

    model_config = {"extra": "forbid"}


def get_db(request: Request):
    connection = connect(request.app.state.db_path)
    try:
        yield connection
    finally:
        connection.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("/health")
def health(db: Db):
    db.execute("SELECT 1").fetchone()
    return {"status": "ok"}


@router.get("/categories")
def categories(db: Db):
    rows = db.execute("SELECT slug, title FROM categories ORDER BY title").fetchall()
    return {"items": [dict(row) for row in rows]}


def literal_like(value: str) -> str:
    """Treat %, _ and backslash in search input as text, not LIKE wildcards."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/products")
def list_products(
    db: Db,
    q: str = Query(default="", max_length=80),
    category: str | None = None,
    in_stock: bool | None = None,
    min_price: int | None = Query(default=None, ge=0),
    max_price: int | None = Query(default=None, ge=0),
    sort: Literal["name", "price_asc", "price_desc"] = "name",
    limit: int = Query(default=12, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=400, detail="min_price cannot exceed max_price")

    conditions: list[str] = []
    params: list[object] = []
    if q.strip():
        conditions.append("LOWER(name) LIKE ? ESCAPE '\\'")
        params.append(f"%{literal_like(q.strip().lower())}%")
    if category:
        conditions.append("category_slug = ?")
        params.append(category)
    if in_stock is True:
        conditions.append("stock > 0")
    elif in_stock is False:
        conditions.append("stock = 0")
    if min_price is not None:
        conditions.append("price_rub >= ?")
        params.append(min_price)
    if max_price is not None:
        conditions.append("price_rub <= ?")
        params.append(max_price)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    count = db.execute("SELECT COUNT(*) FROM products" + where, params).fetchone()[0]
    order = {
        "name": "name COLLATE NOCASE ASC, id ASC",
        "price_asc": "price_rub ASC, id ASC",
        "price_desc": "price_rub DESC, id ASC",
    }[sort]
    rows = db.execute(
        f"SELECT id, sku, name, category_slug AS category, manufacturer, price_rub, stock "
        f"FROM products{where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {"items": [dict(row) for row in rows], "total": count, "limit": limit, "offset": offset}


@router.get("/products/{product_id}")
def product_details(product_id: int, db: Db):
    row = db.execute(
        "SELECT id, sku, name, category_slug AS category, manufacturer, price_rub, stock "
        "FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return dict(row)


@router.post("/quotes", status_code=201)
def create_quote(quote: QuoteIn, db: Db):
    product = db.execute("SELECT stock FROM products WHERE id = ?", (quote.product_id,)).fetchone()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if product["stock"] < quote.quantity:
        raise HTTPException(status_code=409, detail="Not enough stock")
    with db:  # COMMIT on success; ROLLBACK if inserting fails
        cursor = db.execute(
            """INSERT INTO quotes (customer_name, email, product_id, quantity, comment)
               VALUES (?, ?, ?, ?, ?)""",
            (quote.name, str(quote.email), quote.product_id, quote.quantity, quote.comment),
        )
    return {"id": cursor.lastrowid, "status": "new", "product_id": quote.product_id}


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize(app.state.db_path, app.state.seed_dir)
    yield


def create_app(*, db_path: Path | None = None, seed_dir: Path | None = None) -> FastAPI:
    application = FastAPI(title="Equipment Catalog", version="2.0.0", lifespan=lifespan)
    application.state.db_path = Path(db_path or os.environ.get("CATALOG_DB_PATH", ROOT / "data" / "catalog.sqlite3"))
    application.state.seed_dir = Path(seed_dir or ROOT / "seed")
    application.include_router(router)

    @application.get("/", include_in_schema=False)
    def homepage():
        return FileResponse(ROOT / "catalog" / "index.html")

    return application


app = create_app()
