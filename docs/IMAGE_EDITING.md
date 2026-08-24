# 4Infinity Creative Studios — Conversational Image Editing

Image/poster editing is revision-based. A member never edits a filesystem path and Aura never overwrites the source image.

## User workflow

1. Generate an image/poster/cover/social graphic, or select a completed item already in the member image library.
2. Tell Aura what to change in normal language, for example: `keep everything else the same, make the infinity symbol brighter and move the subtitle lower`.
3. Aura resolves the correct member-owned source job (using `list_my_images` when needed).
4. `edit_image` creates a new child image job and real output file.
5. The original remains available. Parent/child provenance is stored in `image_edit_lineage`.
6. Any new child may itself become the source for another edit, creating a revision tree rather than destroying history.

## API

- `POST /api/image/edit` — create a child revision from a completed member-owned image job.
- `GET /api/image/jobs/{job_id}/lineage` — inspect parent/child revision relationships.
- `GET /api/image/jobs/{job_id}/download` — download a tenant-bound completed image.

The edit request supports natural-language direction, aspect ratio, preservation intent and edit strength. Base uses automatic provider selection and standard controlled editing. Pro can select higher quality/provider and stronger composition reinterpretation where enabled by its feature flags.

## Providers

Provider routing is deliberately independent of project history.

### Local/self-hosted editor

Set these deployment secrets/settings when an owner-approved local image editor is installed:

```text
AURA_IMAGE_EDIT_CMD=
AURA_IMAGE_EDIT_MODEL=local-image-editor
AURA_IMAGE_EDIT_TIMEOUT=900
```

The command receives:

- `AURA_IMAGE_EDIT_SOURCE`
- `AURA_IMAGE_EDIT_OUTPUT`
- `AURA_IMAGE_EDIT_PROMPT`
- `AURA_IMAGE_EDIT_RATIO`
- `AURA_IMAGE_EDIT_QUALITY`
- `AURA_IMAGE_EDIT_STRENGTH`
- `AURA_IMAGE_EDIT_PRESERVE_SUBJECT`
- `AURA_IMAGE_EDIT_PROJECT_ID`

It must create a real image at `AURA_IMAGE_EDIT_OUTPUT` or fail. It must never overwrite `AURA_IMAGE_EDIT_SOURCE`.

### OpenAI image editing

When `OPENAI_API_KEY` is configured, the adapter can use the current image edit endpoint with `AURA_OPENAI_IMAGE_MODEL` (default `gpt-image-2`). The source image is sent as a multipart image input and the returned image becomes a new local child revision.

Model/provider names remain deployment settings rather than project identity. Existing revisions stay usable if the provider changes later.

## Security and privacy

- Source job lookup is always scoped to the signed-in `user_id`.
- A child lineage record is rejected unless both parent and child jobs belong to the same user.
- Raw user-supplied filesystem paths are never accepted by the edit API or Aura tool.
- Source output must resolve inside the configured image output root.
- Source SHA-256 is bound into edit provenance.
- The original image is never overwritten.
- Aura does not claim an edit succeeded until the real editor returns a completed result.

## Next editing increments

The current lineage foundation is designed to extend into masked/inpaint edits, member-uploaded reference images, multi-reference composition, editable text/logo layers, side-by-side compare, named checkpoints, project-wide visual consistency and image-to-video handoff without changing the revision model.
