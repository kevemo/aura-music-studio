# Pulsar-Frequency House — ComfyUI renderer workflows

Pulsar-Frequency House can use a self-hosted ComfyUI server as the model-agnostic image/video execution layer behind Aura directives.

The application does **not** hard-code one image or video model. Instead, the operator exports trusted ComfyUI workflows in **API format** and stores the JSON files in this directory (or in the directory selected by `AURA_COMFYUI_WORKFLOW_DIR`). This lets the deployment move between supported image/video models without changing the public project schema.

## Runtime settings

```env
AURA_COMFYUI_URL=http://127.0.0.1:8188
AURA_COMFYUI_WORKFLOW_DIR=config/comfyui
AURA_COMFYUI_IMAGE_WORKFLOW=image-production.json
AURA_COMFYUI_VIDEO_WORKFLOW=video-production.json
AURA_COMFYUI_TIMEOUT_SECONDS=30
AURA_COMFYUI_DOWNLOAD_TIMEOUT_SECONDS=600
AURA_CREATIVE_MAX_OUTPUT_MB=4096
```

No renderer is advertised as connected unless the relevant workflow file exists and the ComfyUI server is actually reachable when probed.

## Template variables

Trusted workflow JSON may use these placeholders anywhere an API-format node input accepts the corresponding value:

- `{{prompt}}`
- `{{negative_prompt}}`
- `{{seed}}`
- `{{width}}`
- `{{height}}`
- `{{frames}}`
- `{{fps}}`
- `{{project_name}}`
- `{{project_title}}`
- `{{directive_id}}`
- `{{operation}}`

When a JSON value consists only of a placeholder, the native value type is preserved. For example, `"seed": "{{seed}}"` becomes an integer at render time rather than a string.

Additional operator-approved template keys can be supplied through a render request's `variables` object, but the built-in reserved values above are always derived from the Aura directive/request and override user-supplied duplicates.

## Recommended video architecture

Use an API-format workflow that ends in a normal ComfyUI output node and reports the generated file in execution history. The adapter is deliberately generic, so the workflow can be backed by a current ComfyUI-native video stack such as LTX, Wan, Hunyuan, or another compatible model available to the deployment.

For music videos, build workflow variants for at least:

1. text-to-video;
2. image-to-video;
3. image/audio-to-video or lip-sync where supported;
4. video-to-video/detailing;
5. first/last-frame or keyframe-controlled shots;
6. vertical 9:16 social output;
7. cinematic 16:9 output.

Aura stores each scene/shot as an addressable Creative Element, so changing one scene does not require replacing unrelated project elements.

## Recommended image architecture

Maintain workflow variants for:

1. text-to-image;
2. reference-image generation;
3. local/inpaint-style edits;
4. background replacement;
5. cover/poster composition;
6. high-resolution/upscale/detail pass.

The Creative Project layer stores the generated file as a project-relative element and preserves the originating directive, prompt, renderer workflow, parent elements and lineage metadata.

## API lifecycle

The application uses the normal self-hosted ComfyUI API lifecycle:

1. substitute trusted workflow placeholders;
2. submit the workflow to `/prompt`;
3. persist the returned `prompt_id` on the Aura directive;
4. read `/history/{prompt_id}`;
5. collect server-reported output files;
6. stream each output through `/view` into the member's private project directory;
7. register the imported file as a new Creative Element;
8. mark the directive complete only after outputs exist.

Do not commit model weights, personal reference media, generated member output or API secrets to this repository.
