# Live Sound Studio — Video Generation Suite

Video is a first-class creation surface inside Elevate Souls Productions Presents: The Live Sound Studio.

## Product goal

The studio should let a creator move from a song, audio file, image, lyrics, or written concept to a finished video without leaving the Live Sound Studio. The system remains model-agnostic: the studio owns the project state, prompt, references, timing, output metadata, provenance, and edit history while individual video providers can be replaced or routed dynamically.

## Core workflows

1. **Text → Video** — cinematic clips from a written brief.
2. **Image → Video** — animate cover art, posters, photos, artwork, or generated keyframes.
3. **Video → Video** — restyle, extend, reframe, or transform an existing clip when the configured provider supports it.
4. **Song → Music Video** — use the song structure, lyrics, BPM, key, sections, and Project DNA to create a storyboard and synchronized sequence of generated shots.
5. **Lyrics → Lyric Video** — generate a timed lyric treatment around an existing master, cover image, or visual theme.
6. **Audio Visualizer** — render social-ready visualizers from finished Live Sound Studio masters.
7. **Cover Art → Motion Artwork** — animate the release artwork for platform teasers and streaming/social promotion.
8. **Short-form Social Clips** — 9:16 TikTok, Reels, and Shorts clips created from a project, song hook, lyric passage, or uploaded media.
9. **Landscape Release Video** — 16:9 music videos, teasers, trailers, and YouTube-ready exports.
10. **Square Social Video** — 1:1 promotional edits where required.

## Provider architecture

The video layer must not hard-code the product to one vendor. Providers are selected through a router according to capability, quality, latency, price, user plan, and availability.

Initial adapters:

- OpenAI video generation (Sora family) when `OPENAI_API_KEY` is configured.
- Runway video generation when `RUNWAYML_API_SECRET` is configured.
- Local or self-hosted video engines through `AURA_VIDEO_RENDER_CMD`.

Future adapters can be added without changing the project format.

## Project integration

Every generated video belongs to a Live Sound Studio project and can reference:

- final master or stems;
- lyrics and lyric timing;
- BPM, key, meter, sections, energy curve and instrumentation;
- release artwork and approved reference images;
- creator-supplied footage;
- scene prompts, negative prompts and character/style continuity metadata;
- aspect ratio, duration, target platform and export profile.

## Music-video orchestration

Aura should be able to:

1. inspect the song structure;
2. identify intro, verse, pre-chorus, chorus, bridge, breakdown and outro;
3. derive a visual treatment and shot language;
4. create a storyboard and shot list;
5. generate multiple candidate shots;
6. choose or score the best candidates;
7. sequence shots against musical section boundaries and beats;
8. add lyric/caption overlays when requested;
9. render the sequence with the final master audio;
10. export social and release variants from the same project.

## Export targets

At minimum:

- 720×1280 and higher-quality 9:16 portrait;
- 1280×720 and higher-quality 16:9 landscape;
- 1:1 square where supported by render/edit pipeline;
- MP4/H.264 default delivery;
- optional MOV/ProRes mezzanine export when the local render stack supports it;
- captioned and clean versions;
- audio-on and silent promotional variants;
- TikTok/Reels/Shorts presets.

## Safety, rights and provenance

- User-uploaded references must be authorized for use.
- Voice and identity replication follows the Live Sound Studio consent model.
- Provider safety policies are preserved rather than bypassed.
- Generated assets record provider/model, prompt, references, timestamps, source project, and output hash where available.
- Video jobs must fail clearly rather than pretending a placeholder is a finished generation.

## Plan direction

Video generation is part of the creation ecosystem, but generation quotas and provider costs must be enforced separately from deterministic editing/visualizer tools. ESP-comped Base access can include limited creation functionality while higher-cost generative video usage can be tied to Pro or usage credits without exposing ESP-only Creator Network material to ordinary studio customers.
