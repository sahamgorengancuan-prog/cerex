# CereX PC Agentic Core (separate, repo-ready MVP)

**Status: pre-alpha developer prototype, 30 October 2026.** This is a **new, separate codebase** for CereX's future *Localize Agentic in Your PC* engine, not a claim that the released CereX web UAT already supports these features. It does not replace or modify the existing CereX package, its VPS service, or the active Windows desktop.

## What works now

1. **PC Intelligence** (`doctor`): read-only CPU/RAM/disk/battery/process assessment; predicts a conservative worker count; defers tasks when load is high. It does not change BIOS, registry, security tooling, power plans or user processes.
2. **No desktop intrusion**: agent workers are separate low-priority background *processes*, use unique job workspaces, and do not call a GUI automation API or send keyboard/mouse input. **Process separation is not a security sandbox.** GUI agents are rejected until an actual independent VM/desktop/session backend exists.
3. **Job separation**: validated DAG dependencies, concurrent dispatch subject to PC pressure, bounded per-job timeouts, individual result JSON and aggregated mission status.
4. **Agent <-> agent**: SQLite WAL inbox with sender, recipient, acknowledgement and persistent messages. A worker may send a peer request, await an answer and ask the LLM a second time with peer feedback.
5. **Agent <-> LLM**: provider-agnostic OpenAI-compatible chat-completions gateway. Offline mock mode is the default, and `--live` is required for actual model requests. API keys are read from an environment variable, not written into a repository config.
6. **No arbitrary host shell execution from model replies.** LLM responses are data-only. Docker/VM/Windows sandbox integration is future work.

## Quick start on Windows 10/11

Install Python 3.10+ and open this folder in Terminal. Recommended:

```powershell
py -3 -m pip install psutil
py -3 -m cerex_core doctor --max-workers 4
py -3 -m cerex_core plan --config configs/mission_demo.json
py -3 -m cerex_core run --config configs/mission_interagent_demo.json
py -3 -m cerex_core inbox --agent orchestrator
```

You may also double-click `DIAGNOSE_PC_WINDOWS.bat` or `TEST_OFFLINE_WINDOWS.bat`. The offline tests never call a remote LLM. Diagnostics never transmit data outside your PC. On Windows, worker processes request a no-console, below-normal-priority start.

### Real LLM activation (explicit opt-in)

For a local OpenAI-compatible model server, e.g. an Ollama installation exposing `/v1/chat/completions`:

```powershell
$env:CEREX_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
$env:CEREX_LLM_MODEL = "YOUR_LOCAL_MODEL"
# Set provider.base_url and provider.model in your JSON mission too.
py -3 -m cerex_core run --config configs/mission_demo.json --root .cerex-runs/live1 --live
```

Or configure an **HTTPS** OpenAI-compatible provider and set its API key only in a private environment variable:

```powershell
$env:CEREX_LLM_API_KEY = "YOUR_KEY_FROM_SECRET_MANAGER"
# Edit configs/mission_demo.json provider.base_url and model to match provider.
py -3 -m cerex_core run --config configs/mission_demo.json --root .cerex-runs/live2 --live
```

Model API requests may incur cost. No API key is needed for offline demos. This MVP has no production-grade cost metering or spending cap; use a provider-side limit before real use. Avoid company/confidential materials until access controls and DLP are completed.

## Run automated tests

```bash
python -m unittest discover -s tests -v
```

## Architecture

```text
USER / CereX Web UI (future integration)
          |
  CereX Mission Orchestrator
    |          |         |
 PC doctor   DAG jobs   Resource policy
    |          |         |
    +---- Low-priority OS worker processes ----+
         |             |                      |
  Architect agent   Reviewer agent       Integrator agent
         +-------------+----------------------+
                    SQLite event bus
                         |
                Pluggable LLM Gateway
                   Local or HTTPS
```

Each worker gets a directory and receives only the input task, dependency results, and its own mailbox. The model can ask other agents for input and request another reasoning round after a reply. A worker is **not** automatically allowed to execute scripts or navigate a real desktop.

## Current limitations (important)

- **Actual customer PC diagnosis:** not performed; the remote Windows nodes reported OFFLINE on 2026-10-30. The doctor command must run on that PC to report its live condition.
- **No real desktop/session isolation:** headless jobs are non-intrusive; graphical tasks require a separately authorized VM/session and a verified isolation test. Even low-priority host processes can still compete for shared CPU/RAM.
- **Not an OS security sandbox:** workers share host user permissions. No arbitrary code execution, browser/file-editing tools or privileged automation are implemented.
- **No remote CereX web API wiring:** this is a repository-ready local core module. See `docs/INTEGRATION_CONTRACT.md` for proposed APIs.
- **No mature retry policy, budget guard, remote policy sync, credentials vault, log redaction, agent plugin permissions, or Windows release test.** The LLM gateway is only a compatible endpoint adapter, not a guarantee of interoperability with every provider.
- **No background service installed** and no startup/scheduled tasks created.

## Layout

- `cerex_core/doctor.py` - hardware/load snapshot and safe capacity policy
- `cerex_core/orchestrator.py` - DAG scheduler and process separation
- `cerex_core/worker.py` - headless worker with multi-round peer feedback
- `cerex_core/bus.py` - persistent local messages
- `cerex_core/gateway.py` - model adapter and response parsing
- `configs/` - customizable mission examples
- `tests/` - offline regression suite
- `docs/` - architectural requirements, integration, OSS references and next milestones

**Security statement:** a background process != an isolated interactive desktop != a sandbox. CereX must not market those as equivalent.

## Experimental authenticated localhost API

To expose local read-only diagnostics and mission management to a **future** authenticated CereX device connector, set a random 24+ character token privately, then run the loopback server. It deliberately refuses public/LAN binding and external model calls by default.

```powershell
$env:CEREX_CORE_TOKEN = "REPLACE_WITH_A_LONG_RANDOM_LOCALLY_GENERATED_TOKEN"
py -3 -m cerex_core serve --port 8717
```

Requests must use `Authorization: Bearer <your-token>`:

- `GET http://127.0.0.1:8717/health` (authenticated)
- `GET http://127.0.0.1:8717/diagnostics` (authenticated)
- `POST http://127.0.0.1:8717/missions` body `{"mission": <JSON mission config>, "live": false}`
- `GET http://127.0.0.1:8717/missions/<id>` (authenticated)

Do not expose this localhost API to the Internet using an unrestricted reverse proxy. The CereX cloud/device authentication bridge and signed requests are future work. This server is experimental and is not automatically started at login.
