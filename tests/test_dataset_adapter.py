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


def test_numeric_summary_excludes_identifier_columns(tmp_path, monkeypatch):
    """Reproduces a real bug found in testing: National ID and phone
    number columns were being summed/averaged like real quantities
    (e.g. 'summing to over 1.13 trillion' for National IDs), which is
    meaningless. Identifier-looking columns must be excluded; genuinely
    quantitative ones (like a product count) must still be included."""
    monkeypatch.setattr(dataset_adapter, "DATA_ROOT", str(tmp_path))

    csv = (
        "CUST_ID,NATIONAL_ID,PHONE,PRODUCTS\n"
        "C1,1000389608,500159416,2\n"
        "C2,2999386774,599911914,4\n"
        "C3,1500000000,510000000,1\n"
    )
    result = dataset_adapter.run({"dataset_name": "identifier test", "csv_content": csv})

    assert "PRODUCTS" in result["numeric_summary"]
    assert "NATIONAL_ID" not in result["numeric_summary"]
    assert "PHONE" not in result["numeric_summary"]
    assert "CUST_ID" not in result["numeric_summary"]


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


def _signed_up_token(email):
    r = client.post(
        "/auth/signup",
        json={"name": "Reco Tester", "email": email, "password": "secret123"},
    )
    return r.json()["token"]


def test_recommended_action_find_duplicates_reads_landed_silver_no_file_needed(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_adapter, "DATA_ROOT", str(tmp_path))
    token = _signed_up_token("recotester1@example.com")

    # Land a dataset with duplicate-detectable columns first.
    csv = (
        "CUST_ID,FULL_NAME,DOB,PHONE\n"
        "C1,Ahmed Al-Rashidi,1980-01-01,0501111111\n"
        "C2,Ahmed Al-Rashidi,1980-01-01,0502222222\n"
    )
    r = client.post(
        "/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("customers.csv", io.BytesIO(csv.encode()), "text/csv")},
        data={"dataset_name": "action test dataset"},
    )
    assert r.status_code == 200
    assert b"recommendations" in r.content  # find_duplicates should be offered

    # Now click the recommendation -- no file re-attached, should still work
    # by reading the already-landed Silver CSV back from disk.
    r2 = client.post(
        "/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        data={"action": "find_duplicates", "dataset_name": "action_test_dataset"},
    )
    assert r2.status_code == 200
    assert b"duplicate_review" in r2.content


def test_recommended_action_unknown_dataset_fails_cleanly():
    token = _signed_up_token("recotester2@example.com")
    r = client.post(
        "/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        data={"action": "find_duplicates", "dataset_name": "no_such_dataset"},
    )
    assert r.status_code == 200
    assert b"error" in r.content


def test_extract_csv_content_passes_through_csv():
    content, sheet = dataset_adapter.extract_csv_content("plain.csv", CLEAN_CSV.encode("utf-8"))
    assert content == CLEAN_CSV
    assert sheet is None


def test_extract_csv_content_picks_customer_sheet_over_others():
    import io as _io
    import pandas as pd

    customer_df = pd.DataFrame({"id": [1, 2, 2], "name": ["A", "B", "B"]})  # 1 exact dup
    other_df = pd.DataFrame({"domain": ["x", "y"], "score": [1, 2]})

    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        other_df.to_excel(writer, sheet_name="NDI Assessment", index=False)
        customer_df.to_excel(writer, sheet_name="Customer MDM Sheet", index=False)

    content, sheet = dataset_adapter.extract_csv_content("workbook.xlsx", buf.getvalue())
    assert sheet == "Customer MDM Sheet"
    assert "domain" not in content
    assert "name" in content


def test_extract_csv_content_falls_back_to_first_sheet_when_no_customer_sheet():
    import io as _io
    import pandas as pd

    sheet_a = pd.DataFrame({"a": [1, 2]})
    sheet_b = pd.DataFrame({"b": [3, 4]})

    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sheet_a.to_excel(writer, sheet_name="First Sheet", index=False)
        sheet_b.to_excel(writer, sheet_name="Second Sheet", index=False)

    content, sheet = dataset_adapter.extract_csv_content("workbook.xlsx", buf.getvalue())
    assert sheet == "First Sheet"
    assert "a" in content


def test_extract_csv_content_detects_real_header_past_a_title_row():
    """Reproduces the actual bug found in testing: a title row above the
    real headers produced 'Unnamed: N' columns and shifted data. This
    must resolve to the real column names and correctly-aligned data."""
    import io as _io
    import pandas as pd

    df = pd.DataFrame({
        "customer_id": ["C1", "C2", "C3"],
        "name": ["Alice", "Bob", "Carol"],
        "branch": ["Riyadh", "Jeddah", "Dammam"],
    })

    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Customer MDM Sheet", index=False, startrow=2)
        ws = writer.sheets["Customer MDM Sheet"]
        ws["A1"] = "Banking Demo Dataset -- Customer Master Data Management"
        ws["A2"] = "Generated for DataOS 2.0 Demo"

    content, sheet = dataset_adapter.extract_csv_content("workbook.xlsx", buf.getvalue())
    assert sheet == "Customer MDM Sheet"
    header_line = content.split("\n")[0]
    assert "Unnamed" not in header_line
    assert "customer_id" in header_line
    assert "name" in header_line
    assert "branch" in header_line
    assert "Alice" in content


def test_xlsx_upload_flows_through_full_pipeline(tmp_path, monkeypatch):
    import io as _io
    import pandas as pd

    monkeypatch.setattr(dataset_adapter, "DATA_ROOT", str(tmp_path))

    customer_df = pd.DataFrame({
        "customer_id": [f"C{i}" for i in range(10)],
        "name": [f"Name {i}" for i in range(10)],
    })
    dupes = customer_df.iloc[:2]
    customer_df = pd.concat([customer_df, dupes], ignore_index=True)  # 2 exact dups

    buf = _io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        customer_df.to_excel(writer, sheet_name="Customer MDM Sheet", index=False)

    content, sheet = dataset_adapter.extract_csv_content("Banking_Demo.xlsx", buf.getvalue())
    result = dataset_adapter.run({"dataset_name": "xlsx pipeline test", "csv_content": content})

    assert result["duplicate_rows_removed"] == 2
    assert result["rows"] == 10
    assert sheet == "Customer MDM Sheet"
