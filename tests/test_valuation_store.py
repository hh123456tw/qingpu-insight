import json

from qingpu_insight.valuation_store import FileValuationStore


def test_save_and_retrieve_round_trip(tmp_path):
    store = FileValuationStore(tmp_path)
    record = {"transaction_type": "resale", "estimated_total_price_twd": 15000000}
    vid = store.save(record)
    assert len(vid) == 36  # UUID
    retrieved = store.get(vid)
    assert retrieved is not None
    assert retrieved["transaction_type"] == "resale"
    assert retrieved["estimated_total_price_twd"] == 15000000


def test_atomic_write_preserves_on_failure(tmp_path, monkeypatch):
    store = FileValuationStore(tmp_path)
    record = {"transaction_type": "resale", "value": 100}
    vid1 = store.save(record)
    path = tmp_path / f"{vid1}.json"
    original = path.read_text(encoding="utf-8")

    import json as _json
    original_dumps = _json.dumps
    call_count = 0

    def flaky_dumps(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated write failure")
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(_json, "dumps", flaky_dumps)
    try:
        store.save({"transaction_type": "presale", "value": 200})
    except RuntimeError:
        pass

    assert path.read_text(encoding="utf-8") == original
    assert json.loads(original)["value"] == 100


def test_get_nonexistent_returns_none(tmp_path):
    store = FileValuationStore(tmp_path)
    assert store.get("nonexistent-uuid") is None


def test_get_invalid_uuid_returns_none(tmp_path):
    store = FileValuationStore(tmp_path)
    assert store.get("not-a-uuid") is None
