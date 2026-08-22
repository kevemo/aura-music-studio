import json
import os
import shutil
from pathlib import Path

from gradio_client import Client, handle_file

SOURCE = Path(os.environ.get("AURA_SOURCE", "source.mp3")).resolve()
OUTDIR = Path(os.environ.get("AURA_OUTDIR", "generated")).resolve()
OUTDIR.mkdir(parents=True, exist_ok=True)
DURATION = float(os.environ.get("AURA_DURATION", "30"))
MODEL = os.environ.get("AURA_MODEL", "acestep-v15-xl-turbo")
COVER_STRENGTH = float(os.environ.get("AURA_COVER_STRENGTH", "0.72"))

if not SOURCE.exists():
    raise FileNotFoundError(SOURCE)

prompt = (
    "High-end realistic live-band pop-rock backing track. Preserve the source audio's exact form, "
    "tempo, chord changes, entrances, stops and section timing. Real acoustic drum kit with human "
    "velocity and room ambience; warm fingered electric bass; steel-string acoustic guitar; stereo "
    "electric rhythm guitars; tasteful single-note lead-guitar fills and countermelody without "
    "doubling a lead vocal; grand piano; subtle analog synth; cinematic strings and pads; natural "
    "tambourine and percussion. Add restrained wordless backing harmony singers (ooh/ahh) mainly in "
    "choruses and the final build, mixed behind the future lead singer. No lead singer, no spoken "
    "voice, no synthetic MIDI timbre, no EDM drums. Commercial studio production, natural dynamics, "
    "wide stereo image, punchy but not over-limited, clear space in the center for a male lead vocal."
)

# generation_wrapper currently exposes 49 positional inputs in this order.
args = [
    MODEL,                              # 0 selected_model
    "cover",                           # 1 generation_mode
    prompt,                             # 2 simple_query_input (required by UI)
    "en",                              # 3 simple_vocal_language
    prompt,                             # 4 Prompt
    "[Instrumental]",                  # 5 Lyrics: suppress lead lyrics
    96,                                 # 6 BPM
    "F# Major",                        # 7 Key
    "4",                               # 8 4/4
    "en",                              # 9 vocal language
    8,                                  # 10 DiT steps (XL Turbo)
    7.0,                                # 11 guidance
    True,                               # 12 random seed
    "-1",                              # 13 seed
    None,                               # 14 reference audio
    DURATION,                           # 15 duration
    1,                                  # 16 batch size: critical on ZeroGPU
    handle_file(str(SOURCE)),           # 17 source audio
    "",                                 # 18 audio codes
    0.0,                                # 19 repaint start
    -1,                                 # 20 repaint end
    "Fill the audio semantic mask based on the given conditions:", # 21 instruction
    COVER_STRENGTH,                     # 22 audio cover strength
    "cover",                           # 23 task_type
    False,                              # 24 use_adg
    0.0,                                # 25 cfg interval start
    1.0,                                # 26 cfg interval end
    3.0,                                # 27 shift
    "ode",                             # 28 infer method
    "",                                 # 29 custom timesteps
    "mp3",                             # 30 format
    0.75,                               # 31 LM temperature
    False,                              # 32 thinking (ignored for cover)
    2.0,                                # 33 LM CFG
    0,                                  # 34 top-k
    0.9,                                # 35 top-p
    "NO USER INPUT",                   # 36 negative prompt
    True,                               # 37 use CoT metas
    True,                               # 38 use CoT caption
    False,                              # 39 use CoT lyrics
    False,                              # 40 debug
    True,                               # 41 constrained decoding
    False,                              # 42 get scores
    False,                              # 43 get LRC
    0.5,                                # 44 quality threshold / UI advanced
    8,                                  # 45 advanced numeric UI parameter
    "guitar",                          # 46 track selector (unused by cover)
    [],                                 # 47 track list (unused by cover)
    False,                              # 48 advanced checkbox
]
assert len(args) == 49

client = Client("ACE-Step/Ace-Step-v1.5")
print(f"Calling ACE-Step model={MODEL} duration={DURATION}s cover_strength={COVER_STRENGTH}")
result = client.predict(*args, api_name="/generation_wrapper")
print("RESULT_TYPE", type(result).__name__)
print("RESULT_LEN", len(result) if isinstance(result, (tuple, list)) else None)

# Persist a compact description of the returned object.
def serializable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): serializable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [serializable(x) for x in v]
    return repr(v)

with open(OUTDIR / "result.json", "w", encoding="utf-8") as f:
    json.dump(serializable(result), f, indent=2)

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

# The first returned audio component is Sample 1.
idx, src = found[0]
ext = src.suffix.lower() or ".mp3"
dst = OUTDIR / ("ace_step_render" + ext)
shutil.copy2(src, dst)
print("AUDIO_RETURN_INDEX", idx)
print("OUTPUT", dst)
