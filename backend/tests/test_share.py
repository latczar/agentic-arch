"""Tests for shared analyses.

Mostly about the things that go wrong quietly: links that outlive their welcome,
ids that can be walked, and a public write endpoint being treated as free
storage.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.share import MAX_PAYLOAD_BYTES, ShareStore, ShareTooLarge


@pytest.fixture
def store(tmp_path) -> ShareStore:
    return ShareStore(tmp_path / "shares.db")


def test_a_share_comes_back_by_its_id(store: ShareStore):
    record = store.create({"hello": "world"}, title="Invoice intake")

    found = store.get(record.id)
    assert found is not None
    assert found.payload == {"hello": "world"}
    assert found.title == "Invoice intake"


def test_an_unknown_id_is_simply_absent(store: ShareStore):
    assert store.get("does-not-exist") is None


def test_ids_are_long_and_unguessable(store: ShareStore):
    """Sequential ids would let anyone walk the table and read every process."""

    ids = {store.create({"n": i}, title="x").id for i in range(50)}
    assert len(ids) == 50
    assert all(len(share_id) >= 11 for share_id in ids)


def test_an_expired_share_is_treated_as_absent(store: ShareStore):
    record = store.create({"a": 1}, title="x", ttl_days=-1)
    assert store.get(record.id) is None


def test_reading_sweeps_expired_rows(store: ShareStore):
    store.create({"a": 1}, title="old", ttl_days=-1)
    live = store.create({"a": 2}, title="new")

    assert store.count() == 2
    store.get(live.id)
    assert store.count() == 1  # the dead one went on the way past


def test_an_oversized_payload_is_refused(store: ShareStore):
    with pytest.raises(ShareTooLarge):
        store.create({"blob": "x" * (MAX_PAYLOAD_BYTES + 1)}, title="x")


def test_expiry_is_about_a_month_out(store: ShareStore):
    record = store.create({"a": 1}, title="x")
    expected = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs((record.expires_at - expected).total_seconds()) < 60


def test_a_missing_title_does_not_produce_a_blank_one(store: ShareStore):
    assert store.create({"a": 1}, title="").title == "Untitled process"


def test_a_very_long_title_is_trimmed(store: ShareStore):
    assert len(store.create({"a": 1}, title="t" * 500).title) == 200
