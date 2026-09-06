# Rhiannon legacy-resource migration register

Canonical product: **Rhiannon Intelligence Systems** within **Shared Skies Media powered by Elevate Souls Productions**.

This register records selective migration decisions from the 2026-09-06 Legacy Aura → Rhiannon ZIP deep scan. Legacy discovery is not production completion. Current canonical implementation always wins.

| Legacy resource | Recovered capability | Current equivalent | Gap? | Decision | Current Rhiannon component / handoff | Evidence / test | Status |
|---|---|---|---|---|---|---|---|
| `Rhiannon_Legacy_Aura_Base_Mesh_REFERENCE.glb` + structural report | Static visual base, ~10,023 vertices / ~9,997 triangles, embedded texture | `aura_avatar_validator.py` + `aura_avatar_runtime.py` | Yes: provenance/version truth; production rig remains absent | **Rewrite / reference only** | `RhiannonModel.metadata/v1` in `rhiannon_turn_state.py` | `test_legacy_glb_metadata_is_reference_only_and_cannot_be_promoted_without_rig_evidence` | Integrated on feature branch; static GLB explicitly cannot count as production |
| Legacy mood / expression / gesture / response-to-avatar concepts | Mood/state routing, gesture maps, micro-motion concepts | Existing `AuraHost.performance/v1` layered avatar bus | Yes: canonical conversational turn lifecycle | **Rewrite**; do not port duplicate event bus | `RhiannonTurn.state/v1` mapped into existing avatar states | Turn lifecycle, illegal-transition and expression-boundary tests | Integrated on feature branch |
| Legacy browser speech recognition / synthesis patterns | Mic permission, speech fallback, start/stop/interruption concepts | Current fallback voice conversation + Realtime WebRTC voice | Yes: speech job identity, stale-job rejection, explicit interruption | **Rewrite into current paths** | `RhiannonTurnHost`; fallback job-bound playback; WebRTC `response.cancel` on barge-in | `test_browser_voice_paths_share_turn_host_and_support_real_interruption_controls` | Integrated on feature branch; professional voice generation still Chat 2 authority |
| `Rhiannon_Legacy_Aura_Voice_Preview_REFERENCE.mp3` | Historical voice-character reference | Canonical Rhiannon voice identity + Chat 2 voice profile/runtime systems | No Chat 1 modelling authority | **Reference / handoff to Chat 2** | Historical comparison only | Consent/provenance rule in master specification | Mapped; not treated as training material |
| Legacy JSON memory / conversation persistence | CRUD, IDs, timestamps, integrity and recovery ideas | Current canonical private chat/memory/project stores | Mostly already exists | **Already exists / selective ideas only** | Existing canonical stores remain authoritative | Current repository architecture review | Mapped; no JSON-memory replacement |
| Legacy Ollama / OpenAI-compatible routing | Local/remote provider routing and status ideas | Current provider/speech capability contracts | Mostly already exists; health truth still evolves | **Already exists / selectively improve health state** | `RhiannonVoice.capabilities/v1` and current provider diagnostics | Existing fail-closed voice capability tests | Mapped; no second provider registry |
| `Rhiannon_Legacy_Codex_Creative_Archive_Normalized_2026-09-06.json` | 859 creative/speculative/lore records; historical ID 0600 absent | Current project/memory/RAG architecture | Yes: optional provenance-aware archive namespace | **Rewrite later** | Proposed `Legacy Codex Archive` namespace | Future provenance/search tests required | Mapped backlog; not injected as fact |
| Legacy SelfHost backup rotation | Backup/recovery concept | Chat 7 production backup/recovery authority | Legacy implementation defective | **Reject verbatim / handoff concept to Chat 7** | No Chat 1 backup implementation | Deep-scan defect manifest | Rejected for direct port |
| Legacy Complete/Deployment small server | `/health`, provider/memory/chat/file patterns | Current canonical FastAPI/service layers | No parallel-backend gap | **Reject server restoration; reuse bounded patterns only** | Existing API/service architecture | Current architecture review | Rejected as parallel backend |
| Legacy unsafe execution / credential patterns | `eval`, `vm.Script`, shell/exec/plugin and historical embedded credentials | Current security gates / Chat 7 authority | Security defects only | **Reject** | Canonical secure configuration and release gates | Deep-scan security/sanitisation manifests | Rejected; sanitised archives only |

## Current feature-slice contract

This feature branch intentionally does **not** claim a production Rhiannon 3D model. It adds:

- one bounded turn-state protocol for `idle`, `ready`, `listening`, `processing`, `thinking`, `responding`, `speaking`, `interrupted`, `awaiting_permission`, `degraded`, `error` and `recovery`;
- mapping from those states into the existing avatar presentation/performance bus rather than a duplicate controller;
- speech-job identity and stale-job rejection;
- fallback speech interruption with listening resume;
- Realtime WebRTC response cancellation on user barge-in;
- authenticated/private/no-store companion contract metadata;
- versioned, fail-closed metadata for the recovered static GLB reference.

Still required for production 3D completion: a real derivative rig, skin weights, facial controls/blendshapes, canonical viseme targets, authored animations, real timing-driven mouth motion, validated device budgets and production asset persistence.
