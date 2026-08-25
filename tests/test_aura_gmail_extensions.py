from __future__ import annotations

import base64

from aura_music_studio.aura_gmail_extensions import (
    _extract_payload,
    _gmail_search_query,
    _html_to_text,
    read_gmail_message,
)


def _enc(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def test_gmail_plain_text_body_and_attachment_metadata(monkeypatch):
    payload = {
        "mimeType": "multipart/mixed",
        "headers": [
            {"name": "Subject", "value": "Studio update"},
            {"name": "From", "value": "artist@example.com"},
            {"name": "To", "value": "member@example.com"},
        ],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _enc("Hello Kev\nThe mix is ready.")}},
            {
                "mimeType": "application/pdf",
                "filename": "notes.pdf",
                "body": {"attachmentId": "private-attachment-id", "size": 1234},
            },
        ],
    }

    def fake_get(user_id, service, url, params=None):
        assert user_id == "user-1"
        assert service == "gmail"
        assert params == {"format": "full"}
        return {"id": "msg-1", "threadId": "thread-1", "labelIds": ["INBOX"], "payload": payload}

    from aura_music_studio import aura_gmail_extensions as module
    monkeypatch.setattr(module.google, "_get", fake_get)
    result = read_gmail_message("user-1", "msg-1")
    assert result["message"]["subject"] == "Studio update"
    assert "The mix is ready" in result["body"]
    assert result["body_source"] == "text/plain"
    assert result["attachments"][0]["filename"] == "notes.pdf"
    assert result["attachments_downloaded"] is False
    assert result["tokens_exposed"] is False


def test_gmail_html_fallback_strips_markup_and_script():
    text = _html_to_text("<html><body><h1>Hello</h1><script>secret()</script><p>World</p></body></html>")
    assert "Hello" in text
    assert "World" in text
    assert "secret()" not in text
    assert "<h1>" not in text


def test_gmail_payload_prefers_plain_text_over_html():
    payload = {
        "parts": [
            {"mimeType": "text/html", "body": {"data": _enc("<p>HTML version</p>")}},
            {"mimeType": "text/plain", "body": {"data": _enc("Plain version")}},
        ]
    }
    text, source, attachments = _extract_payload(payload)
    assert text == "Plain version"
    assert source == "text/plain"
    assert attachments == []


def test_gmail_search_query_removes_followup_instruction():
    assert _gmail_search_query("Search my email for from:ana proposal and read it") == "from:ana proposal"
    assert _gmail_search_query("Find in Gmail invoice August then summarise it") == "invoice August"
    assert _gmail_search_query("Tell me a joke") is None
