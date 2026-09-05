# Rhian isolated code sandbox

> **Compatibility note:** this document retains the legacy path `docs/AURA_SANDBOX.md` and the existing `AURA_SANDBOX_*` configuration identifiers so deployed integrations do not break. The current assistant-facing identity is **Rhiannon Intelligence Systems / Rhian**.

Rhian Artifacts can store and version code without executing it. Code execution is a separate capability and must never run arbitrary member code inside the Elevate Souls Productions Content Creation Command Center FastAPI process or host shell.

## Deployment contract

Configure a separately isolated sandbox service only after it has resource limits, ephemeral storage and its own containment boundary:

```env
AURA_SANDBOX_URL=
AURA_SANDBOX_TOKEN=
AURA_SANDBOX_TIMEOUT_SECONDS=60
AURA_SANDBOX_MAX_CODE_CHARS=100000
AURA_SANDBOX_MAX_OUTPUT_CHARS=100000
```

When `AURA_SANDBOX_URL` is blank, Rhian reports that code execution is not configured. There is no local fallback.

## Request

Rhian sends a bounded JSON request to:

`POST {AURA_SANDBOX_URL}/v1/execute`

Example shape:

```json
{
  "language": "python",
  "code": "print('hello')",
  "timeout_seconds": 60,
  "network": false,
  "filesystem": "ephemeral"
}
```

If `AURA_SANDBOX_TOKEN` is configured it is sent as a bearer credential. Keep the service private and do not expose that token to members or browser code.

## Response

The adapter consumes only bounded execution metadata/text:

```json
{
  "completed": true,
  "exit_code": 0,
  "stdout": "hello\n",
  "stderr": "",
  "timed_out": false
}
```

Stdout/stderr are truncated to the configured output limits before Rhian receives them.

## Security requirements for the sandbox service

The external sandbox implementation should provide at minimum:

- disposable execution environment per run;
- CPU, memory, process and wall-clock limits;
- no host filesystem mounts;
- ephemeral writable filesystem only;
- no network by default;
- no Docker/Kubernetes control socket exposure;
- non-root execution;
- syscall/container isolation appropriate to the deployment;
- language/runtime allowlist;
- bounded input/output sizes;
- automatic cleanup after every execution;
- no secrets inherited from the main application environment.

The Command Center sends `network=false` and `filesystem=ephemeral`; the sandbox must enforce those constraints independently rather than trusting the request as its only security boundary.

## Rhian tool behaviour

`run_code_artifact` can execute only a private Rhian Artifact whose kind is `code`. The member's latest message must explicitly ask to run/execute/test the code. Reading or explaining code does not authorise execution.

The result always identifies `host_execution=false` and is eligible for the same verified multi-step workflow history as other Rhian tools.

The `AURA_SANDBOX_*` names above are compatibility configuration identifiers, not current public branding. Rename them only through a tested migration with backwards-compatible aliases where required.
