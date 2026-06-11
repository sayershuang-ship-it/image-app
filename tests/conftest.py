import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module


@pytest.fixture()
def client():
    app_module.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
    app_module.init_db()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
