from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from catalog.db import connect
from catalog.main import create_app


@pytest.fixture
def database_path(tmp_path):
    return tmp_path / "test_catalog.sqlite3"


@pytest.fixture
def client(database_path):
    # Fresh SQLite database for every API test; no shared mutable state.
    application = create_app(db_path=database_path)
    with TestClient(application) as http:
        yield http


@pytest.fixture
def db(database_path):
    with closing(connect(database_path)) as connection:
        yield connection
