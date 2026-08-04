import io

from fastapi.testclient import TestClient

from app.main import app
from app.adapters import dataset_adapter

client = TestClient(app)

# Small CSV with one duplicate row and one null -- null rate lands in the
# 10-50% "hold for review" zone (not so empty it'd just be dropped at Gold).
HEAVY_NULL_CSV = (
    "date,product,category,quantity,unit_price,region\n"
    "2026-01-01,Widget,Gadgets,3,9.99,North\n"
    "2026-01-01,Widget,Gadgets,3,9.99,North\n"  # exact duplicate of the row above
    "2026-01-02,Gizmo,Gadgets,,14.50,South\n"
    "2026-01-03,Widget,Gadgets,5,9.99,East\n"
    "2026-01-04,Widget,Gadgets,7,9.99,West\n"
)

# Larger, clean-ish CSV -- low enough null rate to auto-promote to Gold,
# with one column mostly empty so Gold's column-drop curation has
# something real to do.
def _clean_csv(rows: int = 30) -> str:
    lines = ["id,product,category,quantity,unit_price,mostly_empty"]
    for i in range(rows):
        unit_price = "" if i == 0 else "9.99"  # 1/30 = ~3%, under the 10% hold threshold
        mostly_empty = "x" if i < 3 else ""  # 27/30 = 90% empty -> dropped at Gold
        lines.append(f"{i},Widget,Gadgets,{i+1},{unit_price},{mostly_empty}")
    return "\n".join(lines)


CLEAN_CSV = _clean_csv()


def test_dataset_adapter_holds_at_silver_when_too_many_nulls(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_adapter, "DATA_ROOT", str(tmp_path))

    result = dataset_adapter.run({"dataset_name": "heavy null test", "csv_content": HEAVY_NULL_CSV})

    assert result["dataset_name"] == "heavy_null_test"
    assert result["rows"] == 4  # 5 rows minus 1 exact duplicate
    assert result["duplicate_rows_removed"] == 1
    assert result["null_counts"].get("quantity") == 1  # 1 of 4 unique rows null = 25%, in the hold zone
    assert result["stage"] == "silver_held"
    assert result["hold_reason"] is not None
    assert result["numeric_summary"] == {}
    assert (tmp_path / "silver" / "heavy_null_test" / "cleaned.csv").exists()
    assert not (tmp_path / "gold" / "heavy_null_test").exists()


def test_dataset_adapter_auto_promotes_to_gold_and_curates(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_adapter, "DATA_ROOT", str(tmp_path))

    result = dataset_adapter.run({"dataset_name": "clean test", "csv_content": CLEAN_CSV})

    assert result["stage"] == "gold"
    assert "unit_price" in result["numeric_summary"]
    # mostly_empty is 90% null -> dropped from Gold's curated columns
    assert "mostly_empty" in result["dropped_columns"]
    assert (tmp_path / "gold" / "clean_test" / "data.csv").exists()


def test_dataset_adapter_rejects_empty_name():
    try:
        dataset_adapter.run({"dataset_name": "", "csv_content": HEAVY_NULL_CSV})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_dataset_adapter_rejects_missing_csv():
    try:
        dataset_adapter.run({"dataset_name": "x", "csv_content": ""})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_list_datasets_and_promote(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_adapter, "DATA_ROOT", str(tmp_path))

    dataset_adapter.run({"dataset_name": "listable heavy", "csv_content": HEAVY_NULL_CSV})
    dataset_adapter.run({"dataset_name": "listable clean", "csv_content": CLEAN_CSV})

    listing = dataset_adapter.list_datasets({})
    names = {d["dataset_name"] for d in listing["datasets"]}
    assert "listable_heavy" in names
    assert "listable_clean" in names

    one = dataset_adapter.list_datasets({"dataset_name": "listable heavy"})
    assert one["count"] == 1
    assert one["datasets"][0]["stage"] == "silver_held"

    promoted = dataset_adapter.promote_dataset({"dataset_name": "listable heavy"})
    assert promoted["stage"] == "gold"

    after = dataset_adapter.list_datasets({"dataset_name": "listable heavy"})
    assert after["datasets"][0]["stage"] == "gold"


def test_promote_dataset_no_op_when_not_held(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_adapter, "DATA_ROOT", str(tmp_path))

    dataset_adapter.run({"dataset_name": "already gold", "csv_content": CLEAN_CSV})
    result = dataset_adapter.promote_dataset({"dataset_name": "already gold"})

    assert result["already_stage"] == "gold"
    assert "nothing to promote" in result["message"]


def test_promote_dataset_unknown_raises():
    try:
        dataset_adapter.promote_dataset({"dataset_name": "does not exist anywhere"})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_chat_upload_requires_auth():
    r = client.post(
        "/chat/upload",
        files={"file": ("test.csv", io.BytesIO(HEAVY_NULL_CSV.encode()), "text/csv")},
        data={"dataset_name": "test"},
    )
    assert r.status_code == 401


def test_chat_upload_without_llm_key_fails_cleanly():
    r = client.post(
        "/auth/signup",
        json={"name": "Upload Tester", "email": "uploadtester@example.com", "password": "secret123"},
    )
    token = r.json()["token"]

    r = client.post(
        "/chat/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.csv", io.BytesIO(CLEAN_CSV.encode()), "text/csv")},
        data={"dataset_name": "test upload"},
    )
    # No OPENROUTER_API_KEY in the test environment -- clean 503, not a crash.
    # The adapter itself should still have run (data landed) before the
    # explain-in-plain-English step failed.
    assert r.status_code == 503
    assert "OPENROUTER_API_KEY" in r.json()["detail"]


def test_chat_stream_requires_auth():
    r = client.post("/chat/stream", data={"message": "hello"})
    assert r.status_code == 401
