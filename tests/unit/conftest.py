"""Shared fixture: fresh in-memory DB for unit tests that touch the store."""
import pytest

from pulse import db


@pytest.fixture()
def in_memory_db():
    db.reset()
    db.connect(":memory:")
    yield db
    db.reset()
