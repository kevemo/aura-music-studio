from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .localization import (
    DEFAULT_LOCALE,
    LOCALE_COOKIE,
    AuraTranslationService,
    LocalePreferenceStore,
    LocalizationError,
    language_options,
    locale_direction,
    normalize_locale,
)

router = APIRouter(prefix="/localization", tags=["localization"])
accounts = AccountStore()
preferences = LocalePreferenceStore(accounts.db_path)
translator = AuraTranslationService(accounts.db_path)
SESSION_COOKIE = "lss_session"


class LocalePreferenceBody(BaseModel):
    locale: str = Field(min_length=2, max_length=64)


class TranslateBody(BaseModel):
    locale: str | None = Field(default=None, max_length=64)
    texts: list[str] = Field(min_length=1, max_length=200)


def _session_user(request: Request) -> dict | None:
    return accounts.resolve_session(request.cookies.get(SESSION_COOKIE))


def _current_locale(request: Request) -> str:
    user = _session_user(request)
    if user:
        preferred = preferences.get_user_locale(user["id"])
        if preferred:
            return preferred
    cookie_value = request.cookies.get(LOCALE_COOKIE)
    if cookie_value:
        try:
            return normalize_locale(cookie_value)
        except LocalizationError:
            pass
    return DEFAULT_LOCALE


def _set_locale_cookie(response: Response, locale: str) -> None:
    response.set_cookie(
        LOCALE_COOKIE,
        locale,
        max_age=365 * 24 * 60 * 60,
        httponly=False,
        secure=(os.getenv("LSS_COOKIE_SECURE", "true").lower() == "true"),
        samesite="lax",
    )


@router.get("/languages")
def languages():
    options = list(language_options())
    return {
        "default_locale": DEFAULT_LOCALE,
        "count": len(options),
        "languages": options,
        "source": "Unicode CLDR via Babel",
    }


@router.get("/preference")
def locale_preference(request: Request):
    locale = _current_locale(request)
    return {"locale": locale, "direction": locale_direction(locale), "default_locale": DEFAULT_LOCALE}


@router.post("/preference")
def set_locale_preference(body: LocalePreferenceBody, request: Request, response: Response):
    try:
        locale = normalize_locale(body.locale)
    except LocalizationError as exc:
        raise HTTPException(422, str(exc)) from exc
    user = _session_user(request)
    if user:
        preferences.set_user_locale(user["id"], locale)
    _set_locale_cookie(response, locale)
    return {
        "saved": True,
        "locale": locale,
        "direction": locale_direction(locale),
        "persisted_to_account": bool(user),
    }


@router.post("/translate")
def translate_ui(body: TranslateBody, request: Request):
    locale = body.locale or _current_locale(request)
    try:
        return translator.translate(locale, body.texts)
    except LocalizationError as exc:
        raise HTTPException(422, str(exc)) from exc
