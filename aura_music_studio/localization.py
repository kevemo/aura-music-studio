from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import requests
from babel import Locale, UnknownLocaleError, localedata

DEFAULT_LOCALE = "en"
LOCALE_COOKIE = "lss_locale"


class LocalizationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_locale(value: str | None) -> str:
    raw = (value or DEFAULT_LOCALE).strip().replace("_", "-")
    if not raw:
        return DEFAULT_LOCALE
    try:
        parsed = Locale.parse(raw, sep="-")
    except (UnknownLocaleError, ValueError):
        raise LocalizationError(f"Unsupported locale: {raw}")
    return str(parsed).replace("_", "-")


def locale_direction(locale: str) -> str:
    try:
        parsed = Locale.parse(normalize_locale(locale), sep="-")
        return "rtl" if parsed.character_order == "right-to-left" else "ltr"
    except Exception:
        return "ltr"


@lru_cache(maxsize=1)
def language_options() -> tuple[dict, ...]:
    """Return one selector entry per CLDR language/script locale.

    Regional variants inherit the same language choice and are intentionally not expanded into
    hundreds of near-duplicate country entries. Script variants such as Simplified/Traditional
    Chinese remain available because they materially change the written UI.
    """
    english = Locale.parse("en")
    result: list[dict] = []
    seen: set[str] = set()
    for identifier in localedata.locale_identifiers():
        if identifier == "root":
            continue
        try:
            item = Locale.parse(identifier)
        except (UnknownLocaleError, ValueError):
            continue
        if item.territory or item.variant:
            continue
        tag = str(item).replace("_", "-")
        if tag in seen:
            continue
        seen.add(tag)
        try:
            native_name = item.get_display_name(item) or tag
        except Exception:
            native_name = tag
        try:
            english_name = item.get_display_name(english) or native_name
        except Exception:
            english_name = native_name
        result.append(
            {
                "locale": tag,
                "language": item.language,
                "script": item.script,
                "native_name": native_name,
                "english_name": english_name,
                "direction": "rtl" if item.character_order == "right-to-left" else "ltr",
            }
        )
    result.sort(key=lambda row: (row["english_name"].casefold(), row["locale"]))
    # English is the product default and stays first in every selector.
    result.sort(key=lambda row: 0 if row["locale"] == DEFAULT_LOCALE else 1)
    return tuple(result)


class LocalePreferenceStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_locale_preferences (
                    user_id TEXT PRIMARY KEY,
                    locale TEXT NOT NULL DEFAULT 'en',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS localization_cache (
                    cache_key TEXT PRIMARY KEY,
                    locale TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_localization_cache_locale
                    ON localization_cache(locale, updated_at DESC);
                """
            )

    def get_user_locale(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT locale FROM user_locale_preferences WHERE user_id=?", (user_id,)
            ).fetchone()
        return normalize_locale(row["locale"]) if row else None

    def set_user_locale(self, user_id: str, locale: str) -> str:
        locale = normalize_locale(locale)
        with self._connect() as con:
            con.execute(
                """INSERT INTO user_locale_preferences(user_id,locale,updated_at)
                   VALUES (?,?,?) ON CONFLICT(user_id)
                   DO UPDATE SET locale=excluded.locale,updated_at=excluded.updated_at""",
                (user_id, locale, _now()),
            )
        return locale

    @staticmethod
    def _cache_key(locale: str, text: str) -> str:
        return hashlib.sha256(f"{locale}\0{text}".encode("utf-8")).hexdigest()

    def cached(self, locale: str, texts: Iterable[str]) -> dict[str, str]:
        values = list(dict.fromkeys(texts))
        if not values:
            return {}
        keys = [(self._cache_key(locale, text), text) for text in values]
        result: dict[str, str] = {}
        with self._connect() as con:
            for key, text in keys:
                row = con.execute(
                    "SELECT translated_text FROM localization_cache WHERE cache_key=?", (key,)
                ).fetchone()
                if row:
                    result[text] = row["translated_text"]
        return result

    def save_translations(self, locale: str, mapping: dict[str, str], provider: str) -> None:
        now = _now()
        with self._connect() as con:
            for source, translated in mapping.items():
                con.execute(
                    """INSERT INTO localization_cache
                       (cache_key,locale,source_text,translated_text,provider,updated_at)
                       VALUES (?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                       translated_text=excluded.translated_text,provider=excluded.provider,updated_at=excluded.updated_at""",
                    (self._cache_key(locale, source), locale, source, translated, provider, now),
                )


class AuraTranslationService:
    """Cached, provider-failover UI translation for every CLDR language choice."""

    def __init__(self, db_path: str | Path | None = None):
        self.store = LocalePreferenceStore(db_path)

    @staticmethod
    def _target_name(locale: str) -> str:
        parsed = Locale.parse(locale, sep="-")
        return f"{parsed.get_display_name(Locale.parse('en'))} ({parsed.get_display_name(parsed)})"

    @staticmethod
    def _extract_openai_text(payload: dict) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        texts: list[str] = []
        for item in payload.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    texts.append(str(content["text"]))
        return "\n".join(texts).strip()

    def _openai_translate(self, locale: str, texts: list[str]) -> list[str]:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise LocalizationError("OPENAI_API_KEY is not configured")
        model = (
            os.getenv("AURA_TRANSLATION_MODEL")
            or os.getenv("AURA_OPENAI_CHAT_MODEL")
            or "gpt-5-mini"
        )
        schema = {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": len(texts),
                    "maxItems": len(texts),
                }
            },
            "required": ["translations"],
            "additionalProperties": False,
        }
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "instructions": (
                    "Translate software-interface text accurately and naturally. Preserve product names "
                    "Aura, Elevate Souls Productions and The Live Sound Studio, URLs, numbers, emoji, "
                    "file extensions and technical identifiers. Return one translation for every input "
                    "in the same order. Do not add commentary."
                ),
                "input": json.dumps(
                    {"target_locale": locale, "target_language": self._target_name(locale), "texts": texts},
                    ensure_ascii=False,
                ),
                "text": {"format": {"type": "json_schema", "name": "translations", "schema": schema, "strict": True}},
                "reasoning": {"effort": "low"},
                "max_output_tokens": 12000,
                "store": False,
            },
            timeout=int(os.getenv("AURA_TRANSLATION_TIMEOUT", "120")),
        )
        if response.status_code >= 300:
            raise LocalizationError(
                f"OpenAI translation failed ({response.status_code}): {response.text[:300]}"
            )
        raw = self._extract_openai_text(response.json())
        payload = json.loads(raw)
        translated = payload.get("translations") or []
        if len(translated) != len(texts):
            raise LocalizationError("Translation provider returned the wrong number of strings")
        return [str(value) for value in translated]

    def _ollama_translate(self, locale: str, texts: list[str]) -> list[str]:
        base = (os.getenv("OLLAMA_BASE_URL") or "").rstrip("/")
        if not base:
            raise LocalizationError("OLLAMA_BASE_URL is not configured")
        model = os.getenv("AURA_TRANSLATION_OLLAMA_MODEL", os.getenv("AURA_OLLAMA_MODEL", "qwen3:4b"))
        response = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You translate user-interface strings. Preserve Aura, Elevate Souls Productions, "
                            "The Live Sound Studio, URLs, numbers, emoji and technical identifiers. Return JSON "
                            "with exactly one string in translations for every input string, same order."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"target_locale": locale, "target_language": self._target_name(locale), "texts": texts},
                            ensure_ascii=False,
                        ),
                    },
                ],
                "options": {"temperature": 0.1},
            },
            timeout=int(os.getenv("AURA_TRANSLATION_TIMEOUT", "120")),
        )
        response.raise_for_status()
        payload = json.loads(response.json()["message"]["content"])
        translated = payload.get("translations") or []
        if len(translated) != len(texts):
            raise LocalizationError("Local translation model returned the wrong number of strings")
        return [str(value) for value in translated]

    def translate(self, locale: str, texts: list[str]) -> dict:
        locale = normalize_locale(locale)
        if locale == DEFAULT_LOCALE:
            return {"locale": locale, "provider": "source", "translations": texts, "translated": True}
        if len(texts) > 200:
            raise LocalizationError("At most 200 UI strings may be translated per request")
        cleaned = [str(text)[:800] for text in texts]
        if sum(len(text) for text in cleaned) > 30000:
            raise LocalizationError("Translation request is too large")

        cached = self.store.cached(locale, cleaned)
        missing = list(dict.fromkeys(text for text in cleaned if text not in cached and text.strip()))
        provider = "cache"
        if missing:
            translated: list[str] | None = None
            errors: list[str] = []
            if os.getenv("OPENAI_API_KEY"):
                try:
                    translated = self._openai_translate(locale, missing)
                    provider = "openai"
                except Exception as exc:
                    errors.append(f"openai: {exc}")
            if translated is None and os.getenv("OLLAMA_BASE_URL"):
                try:
                    translated = self._ollama_translate(locale, missing)
                    provider = "ollama"
                except Exception as exc:
                    errors.append(f"ollama: {exc}")
            if translated is None:
                return {
                    "locale": locale,
                    "provider": "unavailable",
                    "translations": cleaned,
                    "translated": False,
                    "warning": "No configured translation engine could translate this locale.",
                    "errors": errors,
                }
            fresh = dict(zip(missing, translated, strict=True))
            self.store.save_translations(locale, fresh, provider)
            cached.update(fresh)

        return {
            "locale": locale,
            "provider": provider,
            "translations": [cached.get(text, text) for text in cleaned],
            "translated": True,
        }
