# Integrating separate CereX PC Agentic Core with current CereX Web UAT

This document is a **proposed contract, not deployed API behavior**.

## Components and trust boundaries

1. Current CereX web/control plane is the account, tenant and mission-management surface. Leave it unchanged in this MVP.
2. PC Core is a separate per-device service (future service wrapper) that evaluates resources and schedules agents locally; it may also connect to a user-configured local model or external API.
3. CereX's signed, authenticated control channel should transport only authorized mission definitions, status and event acknowledgments. Never send user desktop content or host command execution rights by default.
4. Model credentials stay in the local secret manager / OS credential vault and are not written into exported mission JSON, logs or chat.

## Proposed endpoints (NOT IMPLEMENTED)

- `GET /v1/devices/{id}/diagnostics` => CPU/RAM/disk/battery/worker recommendation and check time
- `POST /v1/devices/{id}/missions` => idempotent mission UUID, role configs, DAG, budget and permissions
- `GET /v1/missions/{id}` => queued/running/deferred/blocked/completed/failed, jobs, outputs
- `GET /v1/missions/{id}/events` => authenticated event stream, sequence IDs and redactions
- `POST /v1/missions/{id}/control` => pause/resume/cancel via verified device session

## Device policy schema target

- `isolation_mode`: `headless-only` (available), `docker` (future), `vm` (future), `virtual-desktop` (future)
- `max_workers`, `max_cpu_percent`, `max_ram_percent`, `min_disk_free_gb`, `battery_floor_percent`
- `model_routes`: role => provider, model, max tokens, spending budget and fallback
- `job_graph`: unique IDs, dependencies, input/output artifacts, timeouts, retry policy
- `permission_policy`: per-agent filesystem mounts, network hosts, tool allowlist, required approvals
- `interaction_policy`: forbid host desktop input by default; explicit external isolated backend required

## Acceptance gates

A) Windows PC telemetry accurate against Task Manager and resource pressure defers jobs.
B) Local user simultaneously uses desktop while multiple headless workers complete tasks; no UI focus steals.
C) Genuine GUI tasks run in independently provisioned VM/session; primary desktop untouched; verify after reconnect/reboot.
D) Agent A sends request to B, B responds, A includes reply in next LLM round; duplicate deliveries and restart tested.
E) Local and cloud model providers validated with realistic authentication, limits, tool schema, reconnection and costs.
F) No unauthorized command execution, host file edits, credential disclosure, or tenant crossover; install/uninstall signed.
