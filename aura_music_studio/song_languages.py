from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from functools import lru_cache

import requests
from babel import Locale, UnknownLocaleError

from .localization import language_options, normalize_locale

# ACE-Step 1.5 official supported vocal-language codes (Aug 2026).
ACE_VOCAL_LANGUAGES = {
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "fa",
    "fi", "fr", "he", "hi", "hr", "ht", "hu", "id", "is", "it", "ja", "ko",
    "la", "lt", "ms", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "sa",
    "sk", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
    "yue", "zh", "unknown",
}

LANGUAGE_ALIASES = {
    "fil": "tl",
    "iw": "he",
    "in": "id",
    "cmn": "zh",
}


class SongLanguageError(RuntimeError):
    pass


@dataclass(frozen=True)
class SongLanguage:
    requested: str
    locale: str
    language_code: str
    english_name: str
    native_name: str
    direction: str
    ace_vocal_language: str
    ace_direct_support: bool

    def to_dict(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=1)
def _language_name_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for item in language_options():
        for key in (item["locale"], item["english_name"], item["native_name"]):
            index[str(key).strip().casefold()] = item["locale"]
    # Common product-facing names/aliases.
    index.update(
        {
            "english": "en",
            "spanish": "es",
            "french": "fr",
            "german": "de",
            "italian": "it",
            "portuguese": "pt",
            "japanese": "ja",
            "korean": "ko",
            "arabic": "ar",
            "hindi": "hi",
            "bengali": "bn",
            "punjabi": "pa",
            "urdu": "ur",
            "russian": "ru",
            "ukrainian": "uk",
            "vietnamese": "vi",
            "thai": "th",
            "cantonese": "yue",
            "chinese": "zh",
            "mandarin": "zh",
            "traditional chinese": "zh-Hant",
            "simplified chinese": "zh-Hans",
        }
    )
    return index


def resolve_song_language(value: str | None) -> SongLanguage:
    requested = (value or "en").strip() or "en"
    locale_value = _language_name_index().get(requested.casefold(), requested)
    try:
        locale = normalize_locale(locale_value)
        parsed = Locale.parse(locale, sep="-")
    except (ValueError, UnknownLocaleError) as exc:
        raise SongLanguageError(f"Unsupported song language: {requested}") from exc

    base = LANGUAGE_ALIASES.get(parsed.language, parsed.language)
    ace_code = base if base in ACE_VOCAL_LANGUAGES else "unknown"
    english = Locale.parse("en")
    return SongLanguage(
        requested=requested,
        locale=locale,
        language_code=parsed.language,
        english_name=parsed.get_display_name(english) or locale,
        native_name=parsed.get_display_name(parsed) or locale,
        direction="rtl" if parsed.character_order == "right-to-left" else "ltr",
        ace_vocal_language=ace_code,
        ace_direct_support=ace_code != "unknown",
    )


def song_language_from_manifest(manifest) -> SongLanguage:
    dna = getattr(manifest, "project_dna", {}) or {}
    return resolve_song_language(dna.get("song_locale") or dna.get("language") or "en")


class SongLyricAdapter:
    """Adapt supplied lyrics into the selected singing language without changing song identity.

    This is deliberately separate from UI translation: lyrical translation must preserve section
    structure, meaning, singability, rhyme intent and natural pronunciation. If the requested
    adaptation cannot be performed by a configured language model, Aura fails rather than claiming
    the lyrics were translated.
    """

    def adapt(
        self,
        lyrics: str,
        *,
        target: SongLanguage,
        source_language: str | None = "auto",
        preserve_proper_nouns: bool = True,
    ) -> dict:
        text = (lyrics or "").strip()
        if not text:
            return {"lyrics": "", "provider": "none", "target": target.to_dict()}

        source = (source_language or "auto").strip()
        if source.lower() not in {"", "auto", "detect"}:
            try:
                source_info = resolve_song_language(source)
                if source_info.locale == target.locale:
                    return {"lyrics": text, "provider": "source", "target": target.to_dict()}
            except SongLanguageError:
                pass

        errors: list[str] = []
        if os.getenv("OPENAI_API_KEY"):
            try:
                adapted = self._openai(text, target, source, preserve_proper_nouns)
                return {"lyrics": adapted, "provider": "openai", "target": target.to_dict()}
            except Exception as exc:
                errors.append(f"openai: {type(exc).__name__}: {exc}")

        if os.getenv("OLLAMA_BASE_URL"):
            try:
                adapted = self._ollama(text, target, source, preserve_proper_nouns)
                return {"lyrics": adapted, "provider": "ollama", "target": target.to_dict()}
            except Exception as exc:
                errors.append(f"ollama: {type(exc).__name__}: {exc}")

        raise SongLanguageError(
            "Aura cannot adapt supplied lyrics into the selected song language because no usable "
            "translation/songwriting model is configured. " + " | ".join(errors)
        )

    @staticmethod
    def _instructions(target: SongLanguage, source: str, preserve_proper_nouns: bool) -> str:
        proper = "Preserve names and proper nouns unless natural transliteration is required." if preserve_proper_nouns else ""
        return (
            "You are Aura's multilingual lyric adaptation engine. Translate/adapt the supplied ORIGINAL or user-owned lyrics "
            f"into {target.english_name} ({target.native_name}), locale {target.locale}. Source language: {source}. "
            "Preserve every section label such as [Verse], [Chorus], [Bridge]. Preserve meaning, emotional intent and point of view, "
            "but prioritize natural native phrasing, singability, vowel flow, practical syllable density and rhyme where possible. "
            "Do not add explanations, romanization or translation notes. If the lyrics are already in the target language, keep them "
            f"substantially unchanged except for clear singability corrections. {proper} Return lyrics only."
        )

    def _openai(self, text: str, target: SongLanguage, source: str, preserve_proper_nouns: bool) -> str:
        key = os.environ["OPENAI_API_KEY"]
        model = os.getenv("AURA_SONG_LANGUAGE_MODEL") or os.getenv("AURA_OPENAI_CHAT_MODEL") or "gpt-5.6-terra"
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "instructions": self._instructions(target, source, preserve_proper_nouns),
                "input": text,
                "reasoning": {"effort": "low"},
                "max_output_tokens": 12000,
                "store": False,
            },
            timeout=int(os.getenv("AURA_SONG_LANGUAGE_TIMEOUT", "180")),
        )
        if response.status_code >= 300:
            raise SongLanguageError(f"OpenAI lyric adaptation failed ({response.status_code}): {response.text[:400]}")
        data = response.json()
        direct = data.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts: list[str] = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(str(content["text"]))
        result = "\n".join(parts).strip()
        if not result:
            raise SongLanguageError("OpenAI returned no adapted lyrics")
        return result

    def _ollama(self, text: str, target: SongLanguage, source: str, preserve_proper_nouns: bool) -> str:
        base = os.environ["OLLAMA_BASE_URL"].rstrip("/")
        model = os.getenv("AURA_SONG_LANGUAGE_OLLAMA_MODEL", os.getenv("AURA_OLLAMA_MODEL", "qwen3:8b"))
        response = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": self._instructions(target, source, preserve_proper_nouns)},
                    {"role": "user", "content": text},
                ],
                "options": {"temperature": 0.25},
            },
            timeout=int(os.getenv("AURA_SONG_LANGUAGE_TIMEOUT", "180")),
        )
        response.raise_for_status()
        result = str(response.json().get("message", {}).get("content") or "").strip()
        if not result:
            raise SongLanguageError("Local lyric adapter returned no lyrics")
        return result


def pronunciation_prompt(language: SongLanguage) -> str:
    support = (
        f"Use the renderer's native vocal-language code {language.ace_vocal_language}."
        if language.ace_direct_support
        else "The primary renderer does not expose a dedicated code for this language; use multilingual/auto phoneme handling and verify pronunciation."
    )
    return (
        f"Lead vocal language: {language.english_name} ({language.native_name}), locale {language.locale}. "
        "Use natural native pronunciation, stress, vowel shapes, consonants and phrasing; do not sing with an English accent unless explicitly requested. "
        + support
    )
