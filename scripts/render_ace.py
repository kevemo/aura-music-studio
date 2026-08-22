import json
import os
import shutil
import time
from pathlib import Path

from gradio_client import Client, handle_file
from gradio_client.exceptions import AppError

SOURCE = Path(os.environ.get("AURA_SOURCE", "source.mp3")).resolve()
OUTDIR = Path(os.environ.get("AURA_OUTDIR", "generated")).resolve()
OUTDIR.mkdir(parents=True, exist_ok=True)
DURATION = float(os.environ.get("AURA_DURATION", "30"))
MODEL = os.environ.get("AURA_MODEL", "acestep-v15-xl-turbo")
COVER_STRENGTH = float(os.environ.get("AURA_COVER_STRENGTH", "0.78"))
MAX_ATTEMPTS = int(os.environ.get("AURA_MAX_ATTEMPTS", "4"))
RETRY_SECONDS = int(os.environ.get("AURA_RETRY_SECONDS", "45"))
SPACE_CANDIDATES = [s.strip() for s in os.environ.get(
    "AURA_SPACES",
    "critesjosh/ace-step-music-studio,ACE-Step/Ace-Step-v1.5"
).split(",") if s.strip()]

if not SOURCE.exists():
    raise FileNotFoundError(SOURCE)

prompt = (
    "High-end realistic live-band pop-rock backing track, acoustic-karaoke feel. Preserve the source "
    "audio's exact form, 96 BPM pulse, chord changes, entrances, stops and section timing. Guitars "
    "should have the natural feel of instruments physically tuned one half-step down while the concert "
    "harmony follows the supplied guide. Real acoustic drum kit with human velocity and room ambience; "
    "warm fingered electric bass; steel-string acoustic guitar; stereo electric rhythm guitars; tasteful "
    "single-note lead-guitar fills and original countermelody without doubling a lead vocal; grand piano; "
    "subtle analog synth; cinematic strings and pads; natural tambourine and percussion. Add restrained "
    "wordless backing harmony singers (ooh/ahh), mainly in choruses, bridge and final build, mixed behind "
    "the future lead singer. No lead singer, no spoken voice, no MIDI/General-MIDI timbre, no EDM drums. "
    "Commercial studio production, believable human performances, natural dynamics, wide stereo image, "
    "punchy but not over-limited, with clear center space for a male lead vocal."
)

args = [
    MODEL, "cover", prompt, "en", prompt, "[Instrumental]",
    96, "F# Major", "4", "en",
    8, 7.0, True, "-1", None, DURATION, 1,
    handle_file(str(SOURCE)), "", 0.0, -1,
    "Fill the audio semantic mask based on the given conditions:",
    COVER_STRENGTH, "cover", False, 0.0, 1.0, 3.0, "ode", "", "mp3",
    0.75, False, 2.0, 0, 0.9, "NO USER INPUT",
    True, True, False, False, True, False, False, 0.5, 8, "guitar", [], False,
]
assert len(args) == 49

result = None
last_error = None
winning_space = None
for cycle in range(1, MAX_ATTEMPTS + 1):
    for space in SPACE_CANDIDATES:
        print(f"ACE-Step cycle {cycle}/{MAX_ATTEMPTS} host={space} model={MODEL} duration={DURATION}s strength={COVER_STRENGTH}", flush=True)
        try:
            client = Client(space)
            result = client.predict(*args, api_name="/generation_wrapper")
            winning_space = space
            print(f"ACE-Step generation returned successfully from {space}", flush=True)
            break
        except Exception as exc:
            last_error = exc
            msg = str(exc)
            print(f"ACE-Step host error [{space}]: {type(exc).__name__}: {msg}", flush=True)
            # Continue to the next host for queue pressure, sleeping only between full cycles.
            continue
    if result is not None:
        break
    if cycle < MAX_ATTEMPTS:
        print(f"All ACE-Step hosts unavailable; retrying host pool in {RETRY_SECONDS}s", flush=True)
        time.sleep(RETRY_SECONDS)

if result is None:
    raise RuntimeError(f"ACE-Step failed across {SPACE_CANDIDATES} after {MAX_ATTEMPTS} cycles: {last_error}")

print("WINNING_SPACE", winning_space)
print("RESULT_TYPE", type(result).__name__)
print("RESULT_LEN", len(result) if isinstance(result, (tuple, list)) else None)

def serializable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): serializable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [serializable(x) for x in v]
    return repr(v)

with open(OUTDIR / "result.json", "w", encoding="utf-8") as f:
    json.dump({"winning_space": winning_space, "result": serializable(result)}, f, indent=2)

items = result if isinstance(result, (tuple, list)) else [result]
found = []
for i, item in enumerate(items):
    candidates = []
    if isinstance(item, str):
        candidates.append(item)
    elif isinstance(item, dict):
        for k in ("path", "name", "url"):
            if item.get(k):
                candidates.append(item[k])
    else:
        p = getattr(item, "path", None)
        if p:
            candidates.append(p)
    for c in candidates:
        try:
            p = Path(c)
            if p.exists() and p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".flac", ".ogg", ".m4a"}:
                found.append((i, p))
        except Exception:
            pass

if not found:
    raise RuntimeError("ACE-Step returned no local audio file; inspect generated/result.json")

idx, src = found[0]
ext = src.suffix.lower() or ".mp3"
dst = OUTDIR / ("ace_step_render" + ext)
shutil.copy2(src, dst)
print("AUDIO_RETURN_INDEX", idx)
print("OUTPUT", dst)
