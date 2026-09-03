# Aura Music Studio — Smart Warp / Natural Performance Follow

Status: implemented in the Chat 2 bounded Smart Warp engineering wave.

This document describes executable behavior. It is not a roadmap claim and does not replace the shared Command Center entitlement, project, asset, job, audit or release contracts owned by Chat 1.

## Product purpose

Aura Smart Warp lets a user keep the natural timing of a rights-cleared live performance and conform another project audio asset to that timing without destructively rewriting the source recording.

The primary use case is a performer recording guitar, vocal, piano or another musical anchor without a rigid click, then asking Aura to build or align accompaniment so drums, bass, piano, strings and other generated/recorded parts follow the performance's rubato and gradual timing changes rather than drifting against it or forcing it onto one fixed BPM.

## Implemented architecture

### Variable tempo map

`aura_music_studio.tempo_engine.VariableTempoMap` is the durable timing representation for this wave.

It records:

- stable map ID and schema version;
- project-relative source reference only;
- SHA-256 source identity;
- sample rate and duration;
- working BPM;
- fixed-versus-variable classification;
- meter;
- monotonic beat anchors;
- local smoothed BPM per anchor;
- bar and beat position;
- per-anchor confidence;
- timing-analysis engine/version metadata;
- detector variability/range diagnostics.

Maps are persisted under the protected member project at `work/tempo_maps/<performance-input-id>.json`. API responses return only the project-relative reference, never a host filesystem path.

### Natural timing analysis

Performance-input beat timing already extracted during upload is reused when it contains at least four reliable anchors. If it is insufficient, the Smart Warp engine performs fresh local beat/onset analysis.

Local tempo is derived from inter-beat timing. A short robust median smoother and bounded adjacent detector-jump correction suppress isolated false transient spikes while retaining genuine gradual timing movement and rubato.

Smart Warp fails closed when there is not enough timing evidence to build a stable map.

### Real-audio conform

Long-running conform work is not performed synchronously in the request handler. It is submitted as the existing durable `engineering:smart_warp` job type and executed by the shared Command Center engineering worker.

The renderer:

1. resolves a project-scoped real audio asset;
2. resolves a rights-cleared target Performance Input;
3. builds/loads its persisted variable tempo map;
4. analyses the source accompaniment timing (or uses an explicitly supplied source BPM grid);
5. pairs corresponding musical timing anchors;
6. fails closed when source/target beat counts indicate the wrong material was selected;
7. calculates a bounded duration scale for every segment;
8. applies local phase-vocoder time stretching so timing changes independently of pitch;
9. uses short joins between rendered segments;
10. fits the finished result to the target performance duration;
11. writes a lossless 24-bit WAV render;
12. verifies that the original source SHA-256 is unchanged;
13. registers the result in the existing `AssetLibrary` as a rights-traceable project-generated derivative.

The result is genuine waveform audio. MIDI, beat-grid metadata, a waveform placeholder or a job description is not returned as the finished render.

## Public Music API

Existing performance-input routes remain the source of truth. This wave adds:

- `POST /projects/{project_name}/performance-inputs/{input_id}/tempo-map`
  - analyse/reuse timing;
  - persist the map;
  - attach map ID/reference/mode to Performance Input metadata.

- `GET /projects/{project_name}/performance-inputs/{input_id}/tempo-map`
  - retrieve the persisted project-safe map.

The existing engineering submission route accepts operation `smart_warp` with:

- source `asset_id`;
- `target_performance_input_id`;
- optional known `source_bpm`;
- bounded `max_stretch_ratio`;
- bounded join/crossfade duration.

## Admission and entitlements

Smart Warp consumes Chat 1's existing shared controls rather than duplicating them.

- Project isolation: canonical tenant `project_path` and request context.
- Asset ownership/provenance: existing `AssetLibrary` and rights ledger.
- Long-running work: existing `StudioJobQueue` / `AuraJobWorker`.
- Entitlement: existing `MULTITRACK_DAW` feature key. This currently makes Smart Warp part of the enabled Unlimited Pro DAW stack without adding or copying any price constant.
- Priority: existing `PRIORITY_QUEUE` feature.
- Upload/security limits: existing Performance Input upload path and project confinement.

A denied tier is rejected before project existence is looked up by the engineering API.

## Safety and rights behavior

Smart Warp requires a target Performance Input that is rights-confirmed and has a provenance record. The source must be an audio Asset Library record in the same member project.

The engine does not expose provider credentials and does not need an external provider secret for its local conform path.

The engine does not impersonate a voice, clone a performer or copy a reference recording. Voice cloning/identity consent remains a separate Voice Studio control surface.

## Failure boundaries

The engine rejects rather than fabricates output when:

- fewer than four reliable timing anchors exist;
- timing anchors are not musically usable;
- source and target beat counts are implausibly different;
- a segment requires duration scaling outside the configured safe bound;
- source/target files leave the member project boundary;
- source audio is missing/empty;
- the target guide lacks rights/provenance evidence;
- the render becomes silent/invalid;
- the source hash changes during rendering.

## Benchmark-derived workflow expectations

The original Aura implementation was informed only by public product/documentation expectations:

- Ableton Live documents audio warping, tempo automation and audio clips that can lead/follow set tempo.
- Apple Logic Pro documents Smart Tempo modes where the project adapts to a performance or audio regions flex/follow the project.
- Avid Pro Tools documents Elastic Audio transient/tempo analysis and time-compression/expansion for conforming audio to a tempo map.

Those products establish user expectations for non-destructive timing analysis, editable timing anchors and pitch-independent time manipulation. No proprietary source code, assets, model weights or private APIs are used here.

Public references consulted during design:

- https://www.ableton.com/en/live-manual/11/audio-clips-tempo-and-warping/
- https://support.apple.com/guide/logicpro/smart-tempo-lgcp4e829ea1/mac
- https://resources.avid.com/SupportFiles/PT/Pro_Tools_Quick_Reference_Guide.pdf

## Deliberate boundaries of this wave

This is the production Smart Warp foundation, not the claim that the entire final Tempo Engine is complete.

Still owned by later Chat 2 waves:

- interactive DAW warp-marker editing UI;
- manual downbeat correction and bar remapping;
- advanced transient-specific warp modes by source class;
- groove-template extraction/application;
- quantization-strength and humanization controls tied to the variable map;
- tempo-map automation lanes in the full DAW timeline;
- multi-track phase-coherent group warping;
- live low-latency tempo following while musicians are actively performing;
- higher-quality optional local/provider stretch engines selected through the shared provider/capability contracts.

Until those are implemented and tested, they must not be counted as finished merely because this engine provides the underlying variable-tempo and real-audio conform primitive.
