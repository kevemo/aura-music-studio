from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from .web_access import AuraWebGateway

router = APIRouter(prefix="/web", tags=["Aura Web"])


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        elif tag.lower() in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag.lower() in {"p", "li", "h1", "h2", "h3", "h4", "tr", "div"}:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape(" ".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n+", "\n\n", value)
        return value.strip()


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)


class FetchRequest(BaseModel):
    url: HttpUrl
    max_chars: int = Field(default=40_000, ge=1000, le=150_000)
    use_cache: bool = True


def _readable(content_type: str, text: str) -> str:
    if "html" not in (content_type or "").lower():
        return text.strip()
    parser = _TextExtractor()
    try:
        parser.feed(text)
        return parser.text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", text).strip()


@router.get("/diagnostics")
def diagnostics():
    return AuraWebGateway().diagnostics()


@router.post("/search")
def search(request: SearchRequest):
    try:
        results = AuraWebGateway().search(request.query, limit=request.limit)
    except Exception as exc:
        raise HTTPException(503, f"Aura web search unavailable: {type(exc).__name__}: {exc}") from exc
    return {"query": request.query, "results": results, "count": len(results)}


@router.post("/fetch")
def fetch(request: FetchRequest):
    gateway = AuraWebGateway()
    try:
        result = gateway.fetch_text(str(request.url), use_cache=request.use_cache)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Aura could not fetch this public web page: {type(exc).__name__}: {exc}") from exc
    readable = _readable(result.content_type, result.text)
    clipped = readable[: request.max_chars]
    return {
        "url": result.url,
        "status_code": result.status_code,
        "content_type": result.content_type,
        "cached": result.cached,
        "text": clipped,
        "truncated": len(readable) > len(clipped),
        "returned_chars": len(clipped),
    }
