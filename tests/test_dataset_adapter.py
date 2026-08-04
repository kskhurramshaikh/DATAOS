import io

from fastapi.testclient import TestClient

from app.main import app
from app.adapters.dataset_adapter import run as run_dataset_adapter

client = TestClient(app)

SAMPLE_CSV = (
    "date,product,category,quantity,unit_price,region\n"
    "2026-01-01,Widget,Gadgets,3,9.99,North\n"
    "2026-01-02,Gizmo,Gadgets,,14.50,South\n"
    "2026-01-02,Gizmo,Gadgets,,14.50,South\n"  # exact duplicate of the row above
    "2026-01-03,Widget,Gadgets,5,9.99,East\n"
)


def test_dataset_adapter_lands_bronze_silver_gold(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAOS_DATA_ROOT", str(tmp_path))
    import importlib
    import app.adapters.dataset_adapter as mod
    importlib.reload(mod)

    result = mod.run({"dataset_name": "unit test sales", "csv_content": SAMPLE_CSV})

    assert result["dataset_name"] == "unit_test_sales"
    assert result["rows"] == 3  # 4 rows minus 1 exact duplicate
    assert result["duplicate_rows_removed"] == 1
    # Nulls are counted on the original upload (before dedup removes the
    # duplicate row that also happened to have a null) -- so 2, not 1.
    assert result["null_counts"].get("quantity") == 2
    assert "unit_price" in result["numeric_summary"]
    assert (tmp_path / "bronze" / "unit_test_sales").exists()
    assert (tmp_path / "silver" / "unit_test_sales" / "cleaned.csv").exists()
    assert (tmp_path / "gold" / "unit_test_sales" / "data.csv").exists()


def test_dataset_adapter_rejects_empty_name():
    try:
        run_dataset_adapter({"dataset_name": "", "csv_content": SAMPLE_CSV})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_dataset_adapter_rejects_missing_csv():
    try:
        run_dataset_adapter({"dataset_name": "x", "csv_content": ""})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_chat_upload_requires_auth():
    r = client.post(
        "/chat/upload",
        files={"file": ("test.csv", io.BytesIO(SAMPLE_CSV.encode()), "text/csv")},
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
        files={"file": ("test.csv", io.BytesIO(SAMPLE_CSV.encode()), "text/csv")},
        data={"dataset_name": "test upload"},
    )
    # No OPENROUTER_API_KEY in the test environment -- clean 503, not a crash.
    # The adapter itself should still have run (data landed) before the
    # explain-in-plain-English step failed.
    assert r.status_code == 503
    assert "OPENROUTER_API_KEY" in r.json()["detail"]
