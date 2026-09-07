# Chat 3 — Live Render Controls

Aura Image Designer and Aura Video Studio now monitor active creative renders automatically.

## Behaviour

- Poll only directives whose state is `queued` or `running`.
- Poll every 3 seconds while the studio tab is visible.
- Pause background polling when the browser tab is hidden and refresh immediately when it becomes visible again.
- Keep only one monitor loop and one active poll batch per page.
- Prevent duplicate render, refresh, cancel, or import actions for the same directive.
- Surface queued/running activity with an indeterminate progress indicator.
- Surface completed renders as ready to import without requiring manual status checks.
- Preserve a manual **Refresh now** action for an immediate check.
- Surface **Cancel render** only while cancellation is valid (`queued` or `running`).
- Use the server-side prompt-scoped cancellation endpoint; the browser never talks directly to the renderer.
- Stop polling terminal directives (`completed`, `failed`) and directives returned to `ready_for_renderer` after cancellation.

## Safety boundaries

The browser stores no ComfyUI network address, workflow path, or renderer credentials. All status, cancellation, and output import actions stay behind the authenticated Creative Project API. The UI does not issue global renderer interrupts.
