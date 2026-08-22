"""Create a 96-BPM, 107-measure structural guide for the ACE-Step cover renderer.

The guide is deliberately a production skeleton, not the final sound. ACE-Step receives
this audio to preserve exact timing, harmony, section boundaries, dynamics, the semitone
lift, a single-note guitar countermelody, and wordless backing-harmony cues.
"""
from pathlib import Path
import os
import numpy as np
import soundfile as sf

SR = 44100
BPM = 96.0
BEAT = 60.0 / BPM
BAR = 4.0 * BEAT

# 107 measures exactly, transcribed from the supplied score's section map and harmony.
# A tuple means the bar is split evenly between its listed chords.
SECTIONS = [
    ("Intro",       ["F#","D#m","B","C#","F#","D#m","B","C#"]),                    # 1-8
    ("Verse 1",     ["F#","D#m","B","C#","F#","D#m","B","C#"]),                    # 9-16
    ("Pre 1",       ["F#","D#m","B","C#","F#","D#m","B",("E","C#")]),             # 17-24
    ("Chorus 1",    ["F#","D#m","B","C#","F#","D#m","B",("C#","E")]),             # 25-32
    ("Tag + Verse2",["F#","D#m","B","C#","F#","D#m","B","C#","F#","D#m","B","C#"]),# 33-44
    ("Pre 2",       ["F#","D#m","B","C#","F#","D#m","B","E","C#"]),              # 45-53
    ("Chorus 2",    ["F#","D#m","B","C#","F#","D#m","B","C#"]),                    # 54-61
    ("Bridge",      ["F#","A#m","B","G#m","F#","A#m","B","G#m","C#","D"]),        # 62-71
    ("Solo lift",   ["G","Em","C","D","G","Em","C","D"]),                         # 72-79
    ("Final Chorus",["G","Em","C","D"]*4),                                            # 80-95
    ("Outro",       ["G","Em","C","D"]*3),                                            # 96-107
]
assert sum(len(bars) for _, bars in SECTIONS) == 107

# MIDI chord tones in concert pitch. Guitar can be physically tuned Eb while sounding here.
CH = {
    "F#":  [54,58,61,66],
    "D#m": [51,54,58,63],
    "B":   [47,54,59,63],
    "C#":  [49,56,61,65],
    "E":   [52,56,59,64],
    "A#m": [46,53,58,61],
    "G#m": [44,51,56,59],
    "D":   [50,54,57,62],
    "G":   [55,59,62,67],
    "Em":  [52,55,59,64],
    "C":   [48,55,60,64],
}
ROOT = {"F#":42,"D#m":39,"B":35,"C#":37,"E":40,"A#m":34,"G#m":32,"D":38,"G":43,"Em":40,"C":36}

def hz(note): return 440.0 * (2.0 ** ((note-69)/12.0))

def add_tone(buf, start, dur, note, amp, decay=2.0, pan=0.0, harmonics=(1.0,.28,.10)):
    s = max(0, int(start*SR)); n = int(dur*SR)
    if s >= len(buf) or n <= 0: return
    n = min(n, len(buf)-s)
    t = np.arange(n, dtype=np.float32)/SR
    y = np.zeros(n, dtype=np.float32)
    f = hz(note)
    for k,a in enumerate(harmonics,1): y += a*np.sin(2*np.pi*f*k*t)
    env = (1-np.exp(-60*t))*np.exp(-decay*t)
    y *= amp*env
    l = np.sqrt((1-pan)/2); r = np.sqrt((1+pan)/2)
    buf[s:s+n,0] += y*l; buf[s:s+n,1] += y*r

def add_kick(buf,t,amp=.3):
    n=int(.24*SR); x=np.arange(n,dtype=np.float32)/SR
    f=80*np.exp(-9*x)+42; ph=2*np.pi*np.cumsum(f)/SR
    y=np.sin(ph)*np.exp(-16*x)*amp
    s=int(t*SR); e=min(len(buf),s+n); y=y[:e-s]
    if len(y)>0: buf[s:e]+=y[:,None]

def add_snare(buf,t,amp=.18,seed=0):
    n=int(.20*SR); x=np.arange(n,dtype=np.float32)/SR
    rng=np.random.default_rng(seed); y=rng.normal(0,1,n).astype(np.float32)*np.exp(-20*x)*amp
    s=int(t*SR); e=min(len(buf),s+n); y=y[:e-s]
    if len(y)>0: buf[s:e]+=y[:,None]

def add_hat(buf,t,amp=.035,seed=0):
    n=int(.07*SR); x=np.arange(n,dtype=np.float32)/SR
    rng=np.random.default_rng(seed); z=rng.normal(0,1,n).astype(np.float32); y=np.concatenate([[z[0]],np.diff(z)])*np.exp(-45*x)*amp
    s=int(t*SR); e=min(len(buf),s+n); y=y[:e-s]
    if len(y)>0: buf[s:e]+=y[:,None]

bars=[]
for sec, seq in SECTIONS:
    for chord in seq: bars.append((sec,chord))
DURATION = 107*BAR + 1.5
mix=np.zeros((int(DURATION*SR),2),dtype=np.float32)

# Original countermelody pattern: chord-tone guide only, not the copyrighted vocal melody.
lead_pattern = [(0.20,2,.55),(1.25,1,.34),(2.15,0,.38),(3.05,2,.52)]

for bi,(sec,chord_spec) in enumerate(bars):
    t0=bi*BAR
    big = ("Chorus" in sec) or (sec in {"Solo lift","Outro"})
    bridge = sec=="Bridge"
    energy = .58 if "Verse" in sec else .72 if "Pre" in sec else .92 if big else .76

    # Drums: four-on-grid guide with dynamic growth.
    for h in range(8): add_hat(mix,t0+h*BEAT/2,.025*energy,bi*20+h)
    add_kick(mix,t0,.20*energy); add_snare(mix,t0+BEAT,.13*energy,bi)
    add_kick(mix,t0+2*BEAT,.18*energy); add_snare(mix,t0+3*BEAT,.14*energy,bi+999)
    if big: add_kick(mix,t0+2.5*BEAT,.10*energy)

    parts = chord_spec if isinstance(chord_spec, tuple) else (chord_spec,)
    slice_dur = BAR/len(parts)
    for pi,ch in enumerate(parts):
        st=t0+pi*slice_dur; tones=CH[ch]
        # acoustic-like rhythmic guitar guide
        for eighth in range(int((slice_dur/BEAT)*2)):
            tt=st+eighth*BEAT/2
            note=tones[[0,2,1,3][eighth%4]]
            add_tone(mix,tt,.38,note,.075*energy,5.0,-.26,(1,.35,.14))
        # piano chord guide
        for note in tones:
            add_tone(mix,st,min(slice_dur*.92,2.2),note,.055*energy,1.3,.18,(1,.25,.08))
        # bass root / fifth-ish
        add_tone(mix,st,min(.8,slice_dur*.44),ROOT[ch],.16*energy,2.1,0,(1,.18,.04))
        add_tone(mix,st+slice_dur/2,min(.7,slice_dur*.38),ROOT[ch]+7,.10*energy,2.2,0,(1,.15,.03))
        # pad/strings harmonic bed in bigger sections
        if big or bridge or "Pre" in sec:
            for nte in tones[:3]: add_tone(mix,st,min(slice_dur*.95,2.4),nte+12,.025*energy,.45,0,(1,.08,.03))
        # wordless backing-harmony cue: sustained upper chord tones in choruses / bridge
        if big or bridge:
            for j,nte in enumerate(tones[1:3]):
                # smooth, low-harmonic vowel cue for ACE-Step to reinterpret as ooh/aah singers
                add_tone(mix,st,min(slice_dur*.90,2.0),nte+12,.020*energy,.35,(-.35 if j==0 else .35),(1,.04,.01))

    # single-note guitar countermelody cue in all sections, stronger after bridge
    chord_for_lead = parts[-1]
    tones=CH[chord_for_lead]
    for off,idx,durb in lead_pattern:
        amp=.035*energy if "Verse" in sec else .050*energy if big else .042*energy
        add_tone(mix,t0+off*BEAT,durb*BEAT,tones[idx]+12,amp,3.1,.10,(1,.42,.17))

# final fade across outro tail
fade=int(8*SR)
mix[-fade:]*=np.linspace(1,0,fade,dtype=np.float32)[:,None]
mix=np.tanh(mix*1.6)
p=np.max(np.abs(mix));
if p>0: mix*=.86/p

out=Path(os.environ.get("AURA_GUIDE","source.wav"))
sf.write(out,mix,SR,subtype="PCM_16")
print(out)
print("bars=107 duration=",107*BAR,"seconds")
