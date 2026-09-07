from __future__ import annotations

import io
import json

import pytest
from openpyxl import Workbook

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_creator_data_import import (
    ImportConfirm,
    CreatorDataImportStore,
    MAX_COLUMNS,
    MAX_ROWS,
)
from aura_music_studio.esp_creator_data_import_overlay import router as import_router
from aura_music_studio.esp_progress import EspProgressStore, save_progress_upload


def _stores(tmp_path, monkeypatch):
    monkeypatch.setenv("ESP_PROGRESS_ROOT", str(tmp_path / "progress-files"))
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "creator@example.com",
        "Creator",
        "a-very-secure-test-password",
        "base",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    esp = EspStore(accounts)
    progress = EspProgressStore(esp)
    imports = CreatorDataImportStore(str(accounts.db_path), progress)
    return accounts, user, progress, imports


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    wb.close()
    return buffer.getvalue()


def test_csv_stage_detects_metrics_but_does_not_write_progress(tmp_path, monkeypatch):
    _accounts, user, progress, imports = _stores(tmp_path, monkeypatch)
    content = b"Date,Views,Average Watch Seconds,Shares\n2026-08-25,1200,72,14\n2026-08-26,1500,81,18\n"
    item = imports.stage(user["id"], "live-export.csv", content, "text/csv")

    assert item["status"] == "staged"
    assert item["row_count"] == 2
    assert item["detected_mapping"]["views"] == "views"
    assert item["detected_mapping"]["avg_watch_seconds"] == "average_watch_seconds"
    assert item["detected_mapping"]["shares"] == "shares"
    assert item["private_upload_path_exposed"] is False
    assert "upload_path" not in item
    assert progress.list_for_user(user["id"]) == []


def test_json_and_xlsx_are_supported_structured_sources(tmp_path, monkeypatch):
    _accounts, user, _progress, imports = _stores(tmp_path, monkeypatch)
    json_item = imports.stage(
        user["id"],
        "video.json",
        json.dumps({"data": [{"Video Views": 900, "Completion Rate": "41%", "Saves": 12}]}).encode(),
        "application/json",
    )
    assert json_item["source_format"] == "json"
    assert json_item["detected_mapping"]["views"] == "video_views"
    assert json_item["detected_mapping"]["completion_rate"] == "completion_rate"

    xlsx_item = imports.stage(
        user["id"],
        "live.xlsx",
        _xlsx_bytes([["Period", "Peak Viewers", "New Followers"], ["26 Aug", 88, 17]]),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert xlsx_item["source_format"] == "xlsx"
    assert xlsx_item["detected_mapping"]["peak_viewers"] == "peak_viewers"
    assert xlsx_item["detected_mapping"]["new_followers"] == "new_followers"


def test_confirm_requires_explicit_mapping_and_writes_existing_progress_history(tmp_path, monkeypatch):
    _accounts, user, progress, imports = _stores(tmp_path, monkeypatch)
    content = b"Date,Views,Shares\n25 Aug,1000,10\n26 Aug,1400,16\n"
    item = imports.stage(user["id"], "results.csv", content, "text/csv")

    result = imports.confirm(
        user["id"],
        item["id"],
        ImportConfirm(
            kind="video",
            mapping={"views": "views", "shares": "shares"},
            period_column="date",
            default_period_label="Imported video analytics",
            notes="Creator-confirmed TikTok export",
        ),
    )
    assert result["human_confirmation_required"] is True
    assert result["imported_rows"] == 2
    assert result["skipped_rows"] == 0
    rows = progress.list_for_user(user["id"])
    assert len(rows) == 2
    assert {row["period_label"] for row in rows} == {"25 Aug", "26 Aug"}
    assert all(row["kind"] == "video" for row in rows)
    assert all(row["upload_name"] == "results.csv" for row in rows)
    assert rows[0]["metrics"]["views"] in {1000, 1400}

    with pytest.raises(ValueError, match="already been resolved"):
        imports.confirm(
            user["id"],
            item["id"],
            ImportConfirm(kind="video", mapping={"views": "views"}),
        )


def test_unusable_rows_are_skipped_and_negative_values_are_not_imported(tmp_path, monkeypatch):
    _accounts, user, progress, imports = _stores(tmp_path, monkeypatch)
    content = b"Label,Views,Shares\nA,not-a-number,nope\nB,-5,-1\nC,2200,31\n"
    item = imports.stage(user["id"], "mixed.csv", content, "text/csv")
    result = imports.confirm(
        user["id"],
        item["id"],
        ImportConfirm(kind="video", mapping={"views": "views", "shares": "shares"}, period_column="label"),
    )
    assert result["imported_rows"] == 1
    assert result["skipped_rows"] == 2
    rows = progress.list_for_user(user["id"])
    assert rows[0]["period_label"] == "C"
    assert rows[0]["metrics"] == {"shares": 31, "views": 2200}


def test_imports_are_tenant_isolated(tmp_path, monkeypatch):
    accounts, user, progress, imports = _stores(tmp_path, monkeypatch)
    signup = accounts.signup(
        "other@example.com",
        "Other Creator",
        "another-secure-test-password",
        "base",
    )
    other = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    item = imports.stage(user["id"], "private.csv", b"Views\n500\n", "text/csv")

    with pytest.raises(KeyError):
        imports.get(other["id"], item["id"])
    with pytest.raises(KeyError):
        imports.confirm(other["id"], item["id"], ImportConfirm(kind="video", mapping={"views": "views"}))
    assert progress.list_for_user(other["id"]) == []


def test_parser_bounds_rows_and_columns():
    headers = [f"Column {index}" for index in range(MAX_COLUMNS + 5)]
    lines = [",".join(headers)]
    lines.extend(",".join(str(index) for index in range(len(headers))) for _ in range(MAX_ROWS + 5))
    _format, columns, rows, _mapping = CreatorDataImportStore.parse("bounded.csv", "\n".join(lines).encode())
    assert len(columns) == MAX_COLUMNS
    assert len(rows) == MAX_ROWS


def test_xlsx_is_accepted_by_private_progress_upload_whitelist(tmp_path, monkeypatch):
    monkeypatch.setenv("ESP_PROGRESS_ROOT", str(tmp_path / "private-progress"))
    content = _xlsx_bytes([["Views"], [100]])
    safe, path = save_progress_upload("creator-1", "analytics.xlsx", content)
    assert safe == "analytics.xlsx"
    assert path.endswith("analytics.xlsx")


def test_browser_overlay_has_human_confirm_and_reject_routes_and_no_api_only_review_duplicate():
    route_methods = [
        (getattr(route, "path", None), set(getattr(route, "methods", set()) or set()))
        for route in import_router.routes
    ]
    review_gets = [
        (path, methods)
        for path, methods in route_methods
        if path == "/command-center/progress/import/{import_id}" and "GET" in methods
    ]
    assert len(review_gets) == 1
    assert any(
        path == "/command-center/progress/import/{import_id}/confirm" and "POST" in methods
        for path, methods in route_methods
    )
    assert any(
        path == "/command-center/progress/import/{import_id}/reject" and "POST" in methods
        for path, methods in route_methods
    )
    assert any(
        path == "/command-center/api/progress/imports/{import_id}/confirm" and "POST" in methods
        for path, methods in route_methods
    )
