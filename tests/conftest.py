from pathlib import Path

import pytest

from governed_agents.data import build_database, connect_read_only


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_database(tmp_path_factory.mktemp("db") / "agrodemo.sqlite")


@pytest.fixture()
def conn(db_path: Path):
    connection = connect_read_only(db_path)
    yield connection
    connection.close()
