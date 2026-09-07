from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


_PROVIDER_REDIRECTS = 5
_DEFAULT_MAX_AUDIO_BYTES = 512 * 1024 * 1024
_MAX_CONFIGURED_AUDIO_BYTES = 2 * 1024 * 1024 * 1024
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _lyrics() -> str:
    path = (os.getenv("AURA_LYRICS") or "").strip()
    if not path:
        return ""
    p = Path(path)
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def _segments(lyrics: str) -> int:
    explicit = len(re.findall(r"(?m)^\s*\[[^\]]+\]\s*$", lyrics))
    maximum = max(1, int(os.getenv("AURA_YUE_MAX_SEGMENTS", "2")))
    requested = explicit if explicit else 2
    return max(1, min(requested, maximum))


def _effective_port(parsed) -> int:
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    raise ValueError("YuE provider URL must use HTTP(S)")


def _provider_origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        raise ValueError("YuE provider URL must use HTTP(S) with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("YuE provider URLs must not contain embedded credentials")
    return scheme, host, _effective_port(parsed)


def _safe_provider_url(base: str, value: str) -> str:
    base = base.rstrip("/")
    _provider_origin(base)
    candidate = urljoin(base + "/", str(value or "").strip())
    parsed = urlparse(candidate)
    if parsed.fragment:
        raise ValueError("YuE provider download URLs must not contain fragments")
    if _provider_origin(candidate) != _provider_origin(base):
        raise PermissionError("YuE provider response attempted a cross-origin audio download")
    return candidate


def _audio_download_limit() -> int:
    raw = (os.getenv("AURA_YUE_MAX_AUDIO_BYTES") or str(_DEFAULT_MAX_AUDIO_BYTES)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("AURA_YUE_MAX_AUDIO_BYTES must be an integer") from exc
    if value < 4096:
        raise ValueError("AURA_YUE_MAX_AUDIO_BYTES must be at least 4096")
    return min(value, _MAX_CONFIGURED_AUDIO_BYTES)


def _download_provider_audio(*, base: str, audio_url: str, output: Path, headers: dict[str, str], timeout: int = 300) -> None:
    current = _safe_provider_url(base, audio_url)
    max_bytes = _audio_download_limit()
    response = None

    for _ in range(_PROVIDER_REDIRECTS + 1):
        current = _safe_provider_url(base, current)
        response = requests.get(current, headers=headers, stream=True, timeout=timeout, allow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            response.close()
            response = None
            if not location:
                raise ValueError("YuE provider redirect did not include a Location header")
            current = _safe_provider_url(base, urljoin(current, location))
            continue
        break
    else:
        raise ValueError("YuE provider audio download exceeded the redirect limit")

    if response is None:
        raise RuntimeError("YuE provider returned no audio response")

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        with response:
            response.raise_for_status()
            length_header = response.headers.get("content-length")
            if length_header:
                try:
                    content_length = int(length_header)
                except ValueError as exc:
                    raise ValueError("YuE provider returned an invalid Content-Length") from exc
                if content_length < 0 or content_length > max_bytes:
                    raise ValueError("YuE provider audio exceeded AURA_YUE_MAX_AUDIO_BYTES")

            total = 0
            with partial.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("YuE provider audio exceeded AURA_YUE_MAX_AUDIO_BYTES")
                    handle.write(chunk)

        if total < 4096:
            raise RuntimeError("YuE worker returned an empty/invalid audio file")
        partial.replace(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def main() -> int:
    base = (os.getenv("AURA_YUE_API_URL") or "").strip().rstrip("/")
    output = Path(os.environ.get("AURA_OUTPUT", "neural_master_yue.wav"))
    prompt = (os.getenv("AURA_PROMPT") or "").strip()
    lyrics = _lyrics()
    if not base:
        raise RuntimeError("AURA_YUE_API_URL is not configured")
    _provider_origin(base)
    if not lyrics:
        raise RuntimeError("YuE is a lyrics-first renderer and requires user/project lyrics")

    headers = {"Content-Type": "application/json"}
    if os.getenv("YUE_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['YUE_API_KEY']}"
    body = {
        "prompt": prompt,
        "lyrics": lyrics,
        "segments": _segments(lyrics),
        "seed": int(os.getenv("AURA_YUE_SEED", "42")),
        "max_new_tokens": int(os.getenv("AURA_YUE_MAX_NEW_TOKENS", "3000")),
        "stage2_batch_size": int(os.getenv("AURA_YUE_STAGE2_BATCH_SIZE", "4")),
        "stage1_model": os.getenv("AURA_YUE_STAGE1_MODEL", "m-a-p/YuE-s1-7B-anneal-en-cot"),
        "stage2_model": os.getenv("AURA_YUE_STAGE2_MODEL", "m-a-p/YuE-s2-1B-general"),
    }
    response = requests.post(f"{base}/v1/jobs", headers=headers, json=body, timeout=30)
    response.raise_for_status()
    job = response.json()
    job_id = str(job.get("job_id") or "").strip()
    if not _JOB_ID_RE.fullmatch(job_id):
        raise RuntimeError("YuE worker returned an invalid job_id")

    deadline = time.time() + int(os.getenv("AURA_YUE_TIMEOUT", "7200"))
    poll = max(2, int(os.getenv("AURA_YUE_POLL_SECONDS", "5")))
    while time.time() < deadline:
        status = requests.get(
            f"{base}/v1/jobs/{job_id}",
            headers={k: v for k, v in headers.items() if k != "Content-Type"},
            timeout=15,
        )
        status.raise_for_status()
        data = status.json()
        state = str(data.get("status") or "").lower()
        if state == "failed":
            raise RuntimeError(f"YuE generation failed: {data.get('error') or data}")
        if state == "completed":
            audio_url = str(data.get("audio_url") or f"/v1/audio/{job_id}")
            _download_provider_audio(
                base=base,
                audio_url=audio_url,
                output=output,
                headers={k: v for k, v in headers.items() if k != "Content-Type"},
            )
            print(json.dumps({"renderer": "yue", "job_id": job_id, "output": str(output)}))
            return 0
        time.sleep(poll)
    raise TimeoutError(f"YuE generation timed out: {job_id}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"YuE renderer bridge failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
