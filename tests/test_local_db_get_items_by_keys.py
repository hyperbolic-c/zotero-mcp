"""Tests for LocalZoteroReader.get_items_by_keys."""

import sqlite3
import pytest

from zotero_mcp.local_db import LocalZoteroReader, ZoteroItem


def _build_minimal_db(tmp_path) -> str:
    """Create a minimal Zotero-like SQLite database for testing."""
    db_path = str(tmp_path / "zotero.sqlite")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        INSERT INTO itemTypes VALUES (2, 'journalArticle');
        INSERT INTO itemTypes VALUES (3, 'book');

        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        INSERT INTO fields VALUES (1, 'title');
        INSERT INTO fields VALUES (2, 'abstractNote');
        INSERT INTO fields VALUES (3, 'extra');
        INSERT INTO fields VALUES (4, 'DOI');

        CREATE TABLE items (
            itemID INTEGER PRIMARY KEY,
            itemTypeID INTEGER,
            key TEXT UNIQUE,
            dateAdded TEXT,
            dateModified TEXT
        );
        INSERT INTO items VALUES (1, 2, 'AAAA1111', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
        INSERT INTO items VALUES (2, 2, 'BBBB2222', '2024-01-02T00:00:00Z', '2024-01-02T00:00:00Z');
        INSERT INTO items VALUES (3, 3, 'CCCC3333', '2024-01-03T00:00:00Z', '2024-01-03T00:00:00Z');

        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        INSERT INTO itemDataValues VALUES (1, 'Title A');
        INSERT INTO itemDataValues VALUES (2, 'Abstract A');
        INSERT INTO itemDataValues VALUES (3, 'Title B');
        INSERT INTO itemDataValues VALUES (4, 'Title C');

        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        INSERT INTO itemData VALUES (1, 1, 1);
        INSERT INTO itemData VALUES (1, 2, 2);
        INSERT INTO itemData VALUES (2, 1, 3);
        INSERT INTO itemData VALUES (3, 1, 4);

        CREATE TABLE itemNotes (itemID INTEGER, parentItemID INTEGER, note TEXT);

        CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);

        CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER, orderIndex INTEGER);
    """)
    conn.commit()
    conn.close()
    return db_path


class TestGetItemsByKeys:
    def test_returns_matching_items(self, tmp_path):
        db_path = _build_minimal_db(tmp_path)
        with LocalZoteroReader(db_path=db_path) as reader:
            items = reader.get_items_by_keys(["AAAA1111", "BBBB2222"])
        assert len(items) == 2
        keys = {item.key for item in items}
        assert keys == {"AAAA1111", "BBBB2222"}

    def test_returns_empty_for_empty_keys(self, tmp_path):
        db_path = _build_minimal_db(tmp_path)
        with LocalZoteroReader(db_path=db_path) as reader:
            items = reader.get_items_by_keys([])
        assert items == []

    def test_returns_only_requested_keys(self, tmp_path):
        db_path = _build_minimal_db(tmp_path)
        with LocalZoteroReader(db_path=db_path) as reader:
            items = reader.get_items_by_keys(["AAAA1111"])
        assert len(items) == 1
        assert items[0].key == "AAAA1111"
        assert items[0].title == "Title A"

    def test_ignores_nonexistent_keys(self, tmp_path):
        db_path = _build_minimal_db(tmp_path)
        with LocalZoteroReader(db_path=db_path) as reader:
            items = reader.get_items_by_keys(["AAAA1111", "XXXX9999"])
        assert len(items) == 1
        assert items[0].key == "AAAA1111"

    def test_uses_parameterized_query_not_string_interpolation(self, tmp_path):
        """Verify parameterized query is used by checking a key with SQL metacharacters.

        The key "AAAA1111') OR ('1'='1" contains a single-quote that, if inserted
        via f-string interpolation, would break out of the string literal and
        match ALL rows via the OR clause.  With a parameterized query the key is
        treated as a literal string (which does not exist) and the result is empty.
        """
        db_path = _build_minimal_db(tmp_path)
        # A key with a single-quote that would break string interpolation and
        # exploit the OR condition to return all rows
        injection_key = "AAAA1111') OR ('1'='1"
        with LocalZoteroReader(db_path=db_path) as reader:
            items = reader.get_items_by_keys([injection_key])
        # Parameterized query: treats the whole string as a literal key → no match
        # String interpolation: would produce ...WHERE i.key IN ('AAAA1111') OR ('1'='1')
        #   which would return all rows
        assert items == [], (
            "Expected empty list: key does not exist. If string interpolation is used "
            "the WHERE clause becomes `IN ('AAAA1111') OR ('1'='1')` which returns all rows."
        )
