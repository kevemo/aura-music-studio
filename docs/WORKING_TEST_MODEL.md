# Pulsar-Frequency House — Working Test Model

Production-style interactive preview:

- `https://pulsar-frequency-house-test-model-51sgiwsgi.vercel.app`
- stable project alias reported by the deployment platform: `https://pulsar-frequency-house-test-model-kevin-emerys-projects.vercel.app`

This preview is intentionally a safe interactive model, not the GPU/rendering production deployment. It uses demo data and does not mutate live accounts, publish to social networks, consume generation credits, or expose owner credentials.

## Views to test

Use the role selector in the top navigation to test these boundaries:

1. **Regular Member** — creative tools only: Music Studio, Video Studio, Image Designer and Aura Intelligence. ESP social/agency tools are absent.
2. **ESP Creator** — normal creative tools plus niche-specific training, ESP-only Social Manager and LIVE/Video Progress.
3. **ESP Agent** — ESP Creator functionality plus agent-only Creator Roster oversight.
4. **Mary Admin** — owner dashboard, user access controls, ESP approvals, creator progress and audit using Mary's visual/Aura context.
5. **Kev Admin** — same protected owner capabilities using Kev's visual/Aura context.

## Key interaction checks

- Request ESP verification from Regular Member view; the demo should keep private ESP access locked.
- Switch to ESP Creator and select different niches; training context should remain distinct from regular creative tools.
- Open Social Manager and add demo content to the ESP-only board.
- Switch to ESP Agent and confirm Creator Roster appears only there.
- Switch to Mary/Kev Admin and cycle a user's Free/Base/Pro subscription independently of their ESP role.
- Approve/decline the demo ESP request.
- Open Built vs staged to see which systems are functional architecture versus integrations still requiring production engines/API credentials.

## Current truthful integration state

### Built in the main application

- cross-media Creative DNA/project lineage;
- music/audio production architecture;
- dedicated Image Designer and Video Studio interfaces;
- Aura Intelligence persistence/provider routing;
- ESP self-request + owner approval model;
- ESP niche profiles and niche-specific training context;
- ESP-only social-management data/API layer;
- LIVE/video progress tracking and owner oversight;
- Mary/Kev owner persona switching;
- independent creative-plan and ESP-role controls;
- owner audit foundation;
- professional content-safety baseline;
- opaque server-side owner-session tokens.

### Requires deployment configuration or third-party authorization

- production ComfyUI image/video model workflows and GPU compute;
- live Ollama/OpenAI-compatible Aura Intelligence model endpoint;
- official TikTok/Meta/YouTube/LinkedIn/etc. OAuth publishing, analytics and inbox adapters;
- production secrets, SMTP and payment verification configuration.

The preview should never claim these staged integrations are connected when they are not.
