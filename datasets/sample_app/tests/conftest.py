import sqlite3
from collections.abc import Iterator

import pytest

from authkit.db import connect, migrate


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect()
    migrate(connection)
    yield connection
    connection.close()
