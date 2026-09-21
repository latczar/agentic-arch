"""Tests for the blob-backed share store.

Against a fake rather than the real service, so these cost nothing, need no
account and run offline like everything else here. The fake implements only the
four calls the store makes, which is its own small check: if the store starts
needing a fifth, this stops compiling and somebody has to think about it.

What these can and cannot prove is worth being straight about. They prove the
store's own logic: paths, expiry, size, path traversal, and that a store having
a bad day reads as a dead link rather than a stack trace. They cannot prove the
SDK behaves as documented. Only a deployment does that.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.share import (
    MAX_PAYLOAD_BYTES,
    BlobShareStore,
    ShareRecord,
    ShareTooLarge,
    open_store,
    ShareStore,
)


class FakeBlobClient:
    """The four calls the store makes, and nothing else."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict] = []
        self.deleted: list[str] = []
        self.fail_on_get = False

    def put(self, pathname, body, *, access, content_type=None, add_random_suffix=False, **kw):
        self.puts.append(
            {
                "pathname": pathname,
                "access": access,
                "content_type": content_type,
                "add_random_suffix": add_random_suffix,
            }
        )
        self.objects[pathname] = bytes(body)
        return {"pathname": pathname, "url": f"https://example.invalid/{pathname}"}

    def get(self, pathname, *, access, **kw):
        if self.fail_on_get:
            raise RuntimeError("the blob store is having a bad day")
        if pathname not in self.objects:
            return None
        return _Result(self.objects[pathname])

    def delete(self, pathname, **kw):
        self.deleted.append(pathname)
        self.objects.pop(pathname, None)

    def list_objects(self, *, prefix=None, limit=None, **kw):
        names = [k for k in self.objects if not prefix or k.startswith(prefix)]
        return _Page([_Item(n) for n in names])


class _Result:
    def __init__(self, data: bytes) -> None:
        self.status_code = 200
        # A chunk iterator, which is the awkward shape the real SDK returns.
        self.stream = iter([data[:10], data[10:]])


class _Page:
    def __init__(self, blobs) -> None:
        self.blobs = blobs
        self.has_more = False
        self.cursor = None


class _Item:
    def __init__(self, pathname: str) -> None:
        self.pathname = pathname


@pytest.fixture
def client() -> FakeBlobClient:
    return FakeBlobClient()


@pytest.fixture
def store(client: FakeBlobClient) -> BlobShareStore:
    return BlobShareStore(client=client)


def test_a_share_survives_the_round_trip(store: BlobShareStore):
    record = store.create({"graph": {"title": "Invoice intake"}}, title="Invoice intake")
    back = store.get(record.id)

    assert back is not None
    assert back.payload == {"graph": {"title": "Invoice intake"}}
    assert back.title == "Invoice intake"


def test_the_object_is_named_after_the_id(store: BlobShareStore, client: FakeBlobClient):
    """A random suffix would make the object unfindable from the id alone."""

    record = store.create({"a": 1}, title="x")

    assert client.puts[0]["pathname"] == f"shares/{record.id}.json"
    assert client.puts[0]["add_random_suffix"] is False


def test_shares_are_stored_private(store: BlobShareStore, client: FakeBlobClient):
    store.create({"a": 1}, title="x")
    assert client.puts[0]["access"] == "private"


def test_an_unknown_id_is_not_found(store: BlobShareStore):
    assert store.get("nothing-here") is None


def test_an_expired_share_is_gone_and_swept(store: BlobShareStore, client: FakeBlobClient):
    record = store.create({"a": 1}, title="x", ttl_days=-1)

    assert store.get(record.id) is None
    assert f"shares/{record.id}.json" in client.deleted


def test_a_store_having_a_bad_day_reads_as_a_dead_link(
    store: BlobShareStore, client: FakeBlobClient
):
    """The reader gets a plain message, not a 500 and a stack trace."""

    record = store.create({"a": 1}, title="x")
    client.fail_on_get = True

    assert store.get(record.id) is None


def test_corrupt_content_reads_as_a_dead_link(store: BlobShareStore, client: FakeBlobClient):
    record = store.create({"a": 1}, title="x")
    client.objects[f"shares/{record.id}.json"] = b"this is not json at all"

    assert store.get(record.id) is None


@pytest.mark.parametrize("nasty", ["../secrets", "a/b", "..", ""])
def test_an_id_cannot_describe_a_path(store: BlobShareStore, nasty: str):
    """The id arrives off a URL, so it does not get to pick the object."""

    assert store.get(nasty) is None


def test_an_oversized_payload_is_refused(store: BlobShareStore):
    with pytest.raises(ShareTooLarge):
        store.create({"blob": "x" * (MAX_PAYLOAD_BYTES + 1)}, title="x")


def test_ids_are_not_sequential(store: BlobShareStore):
    ids = {store.create({"a": 1}, title="x").id for _ in range(20)}
    assert len(ids) == 20
    assert all(len(i) >= 12 for i in ids)


def test_expiry_matches_the_other_store(store: BlobShareStore):
    """Both stores enforce the same month, because the limit is the point."""

    record = store.create({"a": 1}, title="x")
    expected = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs((record.expires_at - expected).total_seconds()) < 60


def test_counting_only_sees_shares(store: BlobShareStore, client: FakeBlobClient):
    store.create({"a": 1}, title="x")
    store.create({"a": 2}, title="y")
    client.objects["something/else.json"] = b"{}"

    assert store.count() == 2


def test_a_record_round_trips_through_its_encoding():
    record = ShareRecord(
        id="abc123",
        title="x",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        payload={"a": [1, 2, {"b": None}]},
    )
    assert ShareRecord.decoded(record.encoded()).payload == record.payload


# --- Choosing a store ---------------------------------------------------------


def test_without_a_blob_token_we_get_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("BLOB_STORE_ID", raising=False)

    assert isinstance(open_store(tmp_path / "shares.db"), ShareStore)


def test_a_blob_token_selects_blob_storage(tmp_path, monkeypatch):
    """Vercel injects this when a blob store is attached, so it means deployed."""

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "pretend-token")
    monkeypatch.setattr(
        "app.share.BlobShareStore.__init__",
        lambda self, client=None, prefix="shares": setattr(self, "prefix", prefix),
    )

    assert isinstance(open_store(tmp_path / "shares.db"), BlobShareStore)
