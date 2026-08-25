from __future__ import annotations

from aura_music_studio import aura_connector_extensions as ext


def test_drive_search_query_removes_followup_instruction():
    assert ext._drive_search_query("Search my Drive for campaign brief and read it") == "campaign brief"
    assert ext._drive_search_query("find in google drive quarterly plan and summarize it") == "quarterly plan"


def test_google_doc_read_is_bounded_and_read_only(monkeypatch):
    monkeypatch.setattr(
        ext,
        "_metadata",
        lambda user_id, file_id: {
            "id": file_id,
            "name": "Plan",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-08-25T00:00:00Z",
            "webViewLink": "https://docs.google.com/document/d/test",
            "capabilities": {"canDownload": True},
        },
    )
    monkeypatch.setattr(ext, "_download", lambda *args, **kwargs: (b"hello from drive", "text/plain"))
    result = ext.read_drive_text("user", "file123")
    assert result["text"] == "hello from drive"
    assert result["read_only"] is True
    assert result["added_to_project"] is False
    assert result["exported_as"] == "text/plain"


def test_drive_reader_refuses_download_restriction(monkeypatch):
    monkeypatch.setattr(
        ext,
        "_metadata",
        lambda user_id, file_id: {
            "id": file_id,
            "name": "Restricted",
            "mimeType": "text/plain",
            "capabilities": {"canDownload": False},
        },
    )
    try:
        ext.read_drive_text("user", "file123")
    except PermissionError as exc:
        assert "does not allow" in str(exc)
    else:
        raise AssertionError("Restricted Drive file should not be read")
