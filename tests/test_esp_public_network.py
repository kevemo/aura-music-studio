from __future__ import annotations

import inspect
import sqlite3

from aura_music_studio import esp_public_network as network
from aura_music_studio.accounts import AccountStore


def _isolated(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "network.sqlite3")
    monkeypatch.setattr(network, "_DB_PATH", accounts.db_path)
    monkeypatch.setattr(network, "accounts", accounts)
    monkeypatch.setenv("ESP_PUBLIC_MEDIA_ROOT", str(tmp_path / "media"))
    network._init_schema()
    return accounts


def test_public_page_contains_aura_welcome_and_regional_tiktok_apply_links(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    response = network.esp_network_public()
    html = response.body.decode("utf-8")
    assert "Hear Aura's welcome" in html
    assert "TikTok LIVE Creator Network" in html
    assert "https://www.tiktok.com/t/ZMhoYM4EM/" in html
    assert "https://www.tiktok.com/t/ZMhwyNd68/" in html
    assert "https://www.tiktok.com/t/ZS46fSvqy/" in html
    assert "not already represented by another TikTok LIVE Creator Network" in html
    assert "submitting a form does not guarantee acceptance" in html


def test_only_published_owner_managed_stories_are_public(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    root = network._media_root()
    draft_id = "a" * 32
    pub_id = "b" * 32
    (root / f"{draft_id}.mp4").write_bytes(b"draft")
    (root / f"{pub_id}.mp4").write_bytes(b"published")
    with network._connect() as con:
        for record_id, title, published in ((draft_id, "Private draft", 0), (pub_id, "Published story", 1)):
            con.execute(
                """INSERT INTO esp_public_testimonials
                   (id,title,speaker_name,speaker_type,caption,media_filename,media_type,sha256,published,sort_order,created_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, title, "Creator", "creator", "Experience", f"{record_id}.mp4", "video/mp4", "0" * 64, published, 100, "Kev · ESP Owner"),
            )
    html = network.esp_network_public().body.decode("utf-8")
    assert "Published story" in html
    assert "Private draft" not in html
    assert network.testimonial_media(draft_id).status_code == 404
    assert network.testimonial_media(pub_id).status_code == 200


def test_public_media_reference_is_opaque_and_confined(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    record_id = "c" * 32
    media = network._media_root() / f"{record_id}.webm"
    media.write_bytes(b"webm-data")
    with network._connect() as con:
        con.execute(
            """INSERT INTO esp_public_testimonials
               (id,title,speaker_name,speaker_type,caption,media_filename,media_type,sha256,published,sort_order,created_by)
               VALUES (?,?,?,?,?,?,?,?,1,100,?)""",
            (record_id, "Story", "Owner", "owner", "Caption", media.name, "video/webm", "1" * 64, "Mary · ESP Owner"),
        )
    response = network.testimonial_media(record_id)
    assert response.status_code == 200
    assert str(tmp_path) not in (response.headers.get("content-disposition") or "")


def test_public_network_routes_exist_and_are_composed_into_base_api():
    paths = {getattr(route, "path", None) for route in network.router.routes}
    assert "/esp-network" in paths
    assert "/creator-network" in paths
    assert "/owner/network-stories" in paths
    assert "/owner/network-stories/upload" in paths

    import aura_music_studio.api as aggregate
    source = inspect.getsource(aggregate)
    assert "esp_public_network_router" in source
    assert "app.include_router(esp_public_network_router)" in source
