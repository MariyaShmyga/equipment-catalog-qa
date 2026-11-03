"""Browser tests against a locally started server with its own SQLite file."""

import os
from contextlib import closing
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect, sync_playwright

from catalog.db import connect


@pytest.fixture(scope="session")
def running_server(tmp_path_factory):
    test_db = tmp_path_factory.mktemp("browser-db") / "browser.sqlite3"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "catalog.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        env={**os.environ, "CATALOG_DB_PATH": str(test_db)},
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Test server exited before it could accept requests")
            try:
                with urlopen(base + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.15)
        else:
            raise RuntimeError("Local test server did not start in 15 seconds")
        yield base, test_db
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@pytest.fixture
def page(running_server):
    with sync_playwright() as playwright:
        options = {"headless": True}
        if executable := os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = executable
        browser = playwright.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(running_server[0], wait_until="domcontentloaded")
        expect(page.get_by_test_id("result-count")).to_have_text("Найдено: 48")
        yield page
        browser.close()


@pytest.mark.ui
def test_first_page_and_load_more(page):
    expect(page.get_by_test_id("product-card")).to_have_count(12)
    page.get_by_test_id("load-more").click()
    expect(page.get_by_test_id("product-card")).to_have_count(24)


@pytest.mark.ui
def test_category_filter(page):
    page.get_by_test_id("category").select_option("audio")
    expect(page.get_by_test_id("result-count")).to_have_text("Найдено: 8")
    expect(page.get_by_test_id("product-card")).to_have_count(8)
    expect(page.get_by_text("Wireless Microphone Duo", exact=True)).to_be_visible()


@pytest.mark.ui
def test_search_case_insensitive(page):
    page.get_by_test_id("search").fill("PROJECTOR")
    expect(page.get_by_test_id("result-count")).to_have_text("Найдено: 1")
    expect(page.get_by_test_id("product-card")).to_have_count(1)
    expect(page.get_by_text("Laser Projector 5000", exact=True)).to_be_visible()


@pytest.mark.ui
def test_empty_search_result(page):
    page.get_by_test_id("search").fill("unfindable-xyz")
    expect(page.get_by_test_id("result-count")).to_have_text("Найдено: 0")
    expect(page.get_by_test_id("catalog-message")).to_have_text("Ничего не найдено")
    expect(page.get_by_test_id("product-card")).to_have_count(0)


@pytest.mark.ui
def test_in_stock_filter(page):
    page.get_by_test_id("in-stock").check()
    expect(page.get_by_test_id("result-count")).to_have_text("Найдено: 42")
    page.get_by_test_id("category").select_option("network")
    expect(page.get_by_test_id("result-count")).to_have_text("Найдено: 7")


@pytest.mark.ui
def test_sort_lowest_first(page):
    page.get_by_test_id("sort").select_option("price_asc")
    expect(page.get_by_test_id("product-card").first).to_contain_text("PoE Injector 60W")


@pytest.mark.ui
def test_quote_reaches_database(page, running_server):
    page.get_by_test_id("buyer-name").fill("Maria Buyer")
    page.get_by_test_id("buyer-email").fill("maria@example.org")
    page.get_by_test_id("quote-product").select_option("1")
    page.get_by_test_id("quantity").fill("2")
    page.get_by_test_id("comment").fill("Please call after 14:00")
    page.get_by_test_id("submit-quote").click()
    expect(page.get_by_test_id("quote-status")).to_contain_text("сохранена")
    with closing(connect(running_server[1])) as db:
        saved = db.execute("""SELECT customer_name, email, quantity, comment FROM quotes
                              WHERE email = ? ORDER BY id DESC LIMIT 1""", ("maria@example.org",)).fetchone()
    assert saved is not None
    assert saved["customer_name"] == "Maria Buyer"
    assert saved["quantity"] == 2
    assert saved["comment"] == "Please call after 14:00"


@pytest.mark.ui
def test_html_validation_prevents_invalid_email(page):
    page.get_by_test_id("buyer-name").fill("Valid Buyer")
    page.get_by_test_id("buyer-email").fill("not-an-email")
    page.get_by_test_id("quote-product").select_option("1")
    page.get_by_test_id("submit-quote").click()
    assert page.get_by_test_id("buyer-email").evaluate("el => el.validity.valid") is False
    expect(page.get_by_test_id("quote-status")).to_be_empty()


@pytest.mark.ui
def test_catalog_server_error_is_shown(page):
    page.route("**/api/products?*", lambda route: route.fulfill(status=503, body='{"detail":"Down"}'))
    page.get_by_test_id("search").fill("failure")
    expect(page.get_by_test_id("catalog-message")).to_have_text("Не удалось загрузить каталог")
