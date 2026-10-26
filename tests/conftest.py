from pathlib import Path

import pytest

from gtm_pipeline import loader
from gtm_pipeline.db import connect, init_db

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
AS_OF = "2026-10-26"


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def sample_conn(conn):
    loader.load_all(
        conn,
        SAMPLE / "organizations.csv",
        SAMPLE / "people.csv",
        SAMPLE / "deals.csv",
    )
    return conn
