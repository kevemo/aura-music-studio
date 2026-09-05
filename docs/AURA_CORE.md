# Rhiannon Intelligence Systems / Rhian Core 0.19 — conversational co-creator and Studio operating intelligence

**Product:** Elevate Souls Productions Content Creation Command Center  
**Intelligence layer:** Rhiannon Intelligence Systems (Rhian)  
**Tagline:** Elevate Your Soul Through Purposeful Media

> **Compatibility note:** this document retains the legacy path `docs/AURA_CORE.md` so existing repository links and engineering references do not break. The current public/formal identity is **Rhiannon Intelligence Systems** and the everyday assistant-facing name is **Rhian**. Legacy `/aura-intelligence`, `aura_*` and `AURA_*` identifiers remain compatibility contracts until a tested migration explicitly replaces them.

Rhian Core is the conversational operating layer of the Elevate Souls Productions Content Creation Command Center. It is intentionally broader than a music chatbot: members can use it as a general assistant while also pinning a private Studio project and asking Rhian to inspect, plan or execute connected creative operations.

The design goal is **fast, truthful, private, editable assistance**. Rhian should feel like one continuous AI workspace while the underlying music, image, video, voice, web and ESP systems remain permissioned modules.

## 1. ChatGPT-style workspace features connected in 0.19

The compatibility route `/aura-intelligence` currently provides:

- persistent private conversation threads;
- new, rename, delete and conversation search;
- edit a user message and regenerate the conversation from that point;
- branch a conversation from an earlier message;
- regenerate an assistant answer without re-running the original write tools;
- true token streaming from supported local reasoning models;
- stop-generation in the browser;
- per-conversation project pinning;
- per-conversation Web and Tools switches;
- explicit Rhian Memory management;
- file attachments;
- document extraction for text/code, PDF, DOCX and XLSX;
- image metadata and optional local vision understanding;
- audio metadata and optional automatic local speech transcription;
- sampled-frame video understanding when configured;
- microphone voice input;
- Rhian speech output through the configured local TTS path;
- long-thread summarisation to keep model context efficient;
- tool execution status and a durable tool audit record;
- local Ollama and OpenAI-compatible local-model failover.

This is a **ChatGPT-style product experience**, not a claim that the Command Center copies proprietary OpenAI implementation details or has feature-for-feature parity with every version of ChatGPT.

## 2. Fast-path reasoning

General conversation should not incur an unnecessary planning round trip.

Rhian Core therefore uses three routing layers:

1. **Direct conversational path** — ordinary questions go straight to the reasoning model.
2. **Deterministic obvious tool path** — common commands such as listing projects or checking current information are routed without asking another model to decide whether a tool is needed.
3. **Private tool planner** — only ambiguous project/tool requests use a small JSON planning step.

The browser uses streaming responses, so the member sees the first generated tokens before the complete answer has finished.

## 3. Model backends

Rhian Core is offline/local-first.

Supported reasoning adapters:

- Ollama (`OLLAMA_BASE_URL` plus `AURA_INTELLIGENCE_MODEL` / `AURA_OLLAMA_MODEL`);
- an OpenAI-compatible **local** endpoint (`AURA_LLM_BASE_URL`, `AURA_LLM_MODEL`, optional local endpoint key).

`AURA_INTELLIGENCE_PROVIDER=auto` tries local backends in failover order.

No cloud model is silently assumed. If no reasoning model is reachable, Rhian returns an explicit availability error. If a Studio tool has already completed but the conversational model then fails, the durable tool record is preserved.

## 4. Project-aware conversation

A conversation can pin one member-owned project. Rhian then receives a compact private snapshot rather than requiring the user to restate the project on every turn.

The snapshot can include:

- project title/mode/tempo/key/meter;
- renderer preferences;
- Song DNA summary;
- song sections and instrument-layer IDs;
- pending edit directives;
- DAW track identities, roles, clips and effect counts;
- Creative DNA image/video/audio elements and directives;
- project assets and analysis metadata;
- available outputs.

The snapshot is scoped through the existing tenant storage layer. It must never be assembled by scanning another member's project directory.

## 5. Rhian Memory

Long-term Rhian Memory is **explicit**.

Ordinary conversation is not silently promoted into `aura_memories`. A persistent memory is created only when the user explicitly saves it, for example:

- `Remember that I prefer natural mastering.`
- `Save this to memory: my standard poster ratio is 9:16.`
- the Rhian Memory panel.

Saved memories are visible and deletable by the member.

Thread message history is a separate concept: it is stored because it is the conversation itself, not because Rhian has decided to create a profile about the user.

## 6. Attachments and multimodal context

### Documents

Rhian extracts bounded text from supported text/code files, PDFs, DOCX and XLSX. Extracted text is stored with the private attachment record so repeated turns do not have to parse the file again.

### Images

If `AURA_VISION_MODEL` and the local Ollama endpoint are configured, Rhian can analyse an uploaded image locally. The resulting textual visual analysis is saved with the private attachment and injected into subsequent reasoning.

Visual analysis must not identify a real person from appearance.

### Audio

When local STT is configured, audio attachments can be transcribed while the upload is being prepared. The transcript becomes conversational context. Audio can still be used separately by Studio production tools when it is ingested into a project.

### Video

Rhian can sample chronological frames with FFmpeg and submit those frames to the local vision model. This is explicitly labelled **sampled-frame analysis**, not frame-perfect video inspection.

Automatic video perception is disabled by default because it is substantially more expensive than image/audio preprocessing. It can be enabled with `AURA_CHAT_AUTO_VIDEO_PERCEPTION=true`.

## 7. Web research

Rhian web access reuses the existing protected web gateway rather than exposing unrestricted server-side HTTP requests.

The gateway provides:

- HTTPS-first policy;
- optional operator-controlled HTTP exception;
- DNS resolution and private/local address blocking;
- redirect revalidation;
- allowed/blocked domain policy;
- response size limits;
- request rate limiting;
- cache;
- optional private SearXNG search.

A conversation can turn Web off. When Web is disabled, the tool registry refuses web tools even if a model proposes one.

## 8. Studio tools currently exposed to Rhian Core

### Read/inspect tools

- list member projects;
- inspect a project snapshot;
- inspect complete Song DNA;
- list private project assets/analysis metadata;
- list project outputs;
- protected public-web search/fetch;
- inspect image/video renderer configuration/reachability;
- check image/video render status;
- list consent-controlled Voice Profiles without raw sample paths;
- list/check the member's production jobs.

### Music write/planning tools

- sync DAW state into Song DNA;
- plan a single lyric-line change;
- plan an instrument replacement;
- plan a local section regeneration;
- queue the project through the full production pipeline.

Song edit planning remains non-destructive. Candidate generation, audition and commit retain their dedicated Song DNA workflow.

### Cross-media tools

- create a Creative DNA directive;
- create/queue an image or video;
- check renderer progress;
- import completed renderer outputs into editable Creative DNA elements.

If ComfyUI is not configured, Rhian stores the requested directive but reports `renderer_configured=false`. It must never claim that an image or video was generated when no renderer accepted the job.

## 9. Tool write safety and idempotency

The model is not the sole authority for project mutation.

Runtime write gates independently inspect the member's **latest message**. A write tool is refused when the latest wording did not explicitly request that kind of change.

Examples:

- asking `What would a piano sound like here?` must not replace an instrument;
- asking `Replace the rhythm guitar with piano` may create the non-destructive replacement plan;
- asking `Make a 9:16 cosmic poster` may create/queue an image directive.

Repeated/regenerated answers are hardened:

- Regenerate reuses prior tool results and does not execute the tools again;
- duplicate pending lyric/instrument/section directives are detected;
- duplicate queued visual directives are reused;
- Studio job submission already deduplicates queued/running jobs of the same type/project/user.

## 10. Branch/edit semantics

A branch receives independent copies of files attached to the copied messages. Deleting the original thread therefore does not remove the branch's files.

Editing an earlier user message removes downstream conversation content, invalidates associated stale tool-run records and clears the old summary so the regenerated branch of conversation cannot treat obsolete actions as current evidence.

## 11. Voice safety remains separate from general chat

Rhian Core can inspect Voice Profiles, but voice cloning/conversion itself remains governed by Voice House and the project rights ledger.

Voice Profile rules include:

- explicit consent statement;
- retained consent/verification evidence;
- allowed uses scoped by purpose;
- conservative similarity caps for attested profiles;
- stronger verification state reserved for speaker identity verification/trusted owner review, not phrase transcription alone;
- immediate revocation;
- downstream fail-closed behavior after revocation.

A general chat message cannot bypass these controls.

## 12. ESP separation

Rhian Core is available to ordinary Command Center members, but it does **not** make ESP Creator Network data public to subscribers.

ESP Creator/Agent/Both permissions, niche access, agent-to-creator assignment, owner controls and Social Media Centre access remain governed by the separate ESP permission system.

A future ESP-specific Rhian tool must perform the same permission check as the ESP route it represents; it must never rely only on the language model deciding that a user looks like an agent.

## 13. Truthfulness contract

Rhian must not say an operation succeeded unless the corresponding durable tool result says it succeeded.

Examples:

- `queued` is not `rendered`;
- `rendered` is not `imported`;
- `technical master QC passed` is not proof that humans perceive the performance as indistinguishable from a professional recording;
- `phrase transcribed correctly` is not proof of speaker identity;
- `Creative DNA directive stored` is not proof that an image/video model is installed;
- symbolic MIDI is not a release master.

This contract is part of the architecture, not merely prompt wording.

## 14. Key environment variables

```text
AURA_INTELLIGENCE_PROVIDER=auto
AURA_INTELLIGENCE_MODEL=
AURA_INTELLIGENCE_TIMEOUT=180
OLLAMA_BASE_URL=
AURA_OLLAMA_MODEL=qwen3:4b
AURA_LLM_BASE_URL=
AURA_LLM_MODEL=
AURA_LLM_API_KEY=

AURA_CHAT_ATTACHMENT_DIR=data/aura/attachments
AURA_CHAT_ATTACHMENT_MAX_MB=50
AURA_CHAT_SPEECH_DIR=data/aura/speech
AURA_CHAT_AUTO_PERCEPTION=true
AURA_CHAT_AUTO_VIDEO_PERCEPTION=false

AURA_VISION_MODEL=
AURA_VISION_TIMEOUT=180
AURA_VISION_MAX_IMAGE_MB=12

AURA_SEARXNG_URL=
AURA_WEB_ENABLED=true

AURA_COMFYUI_URL=
AURA_COMFYUI_IMAGE_WORKFLOW=
AURA_COMFYUI_VIDEO_WORKFLOW=

AURA_STT_CMD=
AURA_WHISPER_MODEL=
AURA_TTS_CMD=
AURA_TTS_URL=
AURA_PIPER_MODEL=
```

These `AURA_*` names are compatibility identifiers, not current public branding. Rename them only through an explicit, tested configuration migration with backwards-compatible aliases where required.

## 15. Next capability layers

Rhian Core 0.19 establishes the assistant runtime. The next layers should extend the existing architecture rather than create another chatbot:

1. structured web source cards/citations in the realtime UI;
2. configurable Fast / Auto / Deep / Creative reasoning profiles with concurrency-safe per-thread model selection;
3. safer calculator/data-analysis tools and richer spreadsheet/table analysis;
4. realtime streaming for Edit and Regenerate as well as new messages;
5. project attachment import actions so a chat file can be promoted into the project Asset Library only after the member asks;
6. richer DAW actions (track/fader/effect/automation controls) behind explicit write gates;
7. ESP-specific tools only after mirroring every existing ESP permission boundary;
8. owner-approved speaker verification adapter for higher-confidence Voice Profiles;
9. optional 3D/avatar presentation for Rhian as a UI layer, without coupling the avatar to reasoning/tool correctness.

The critical architectural rule remains: **one Rhian conversation, many permissioned tools, durable editable project state, no fabricated execution.**
