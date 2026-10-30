# CereX PC Agentic Core rollout priorities

| Priority | Capability | Current MVP | Next acceptance criterion |
|---|---|---|---|
| P0 | PC diagnosis, safe capacity scheduler | Code, local tests | Windows live PC comparison and sustained pressure tests |
| P0 | No desktop disruption | Headless processes only | Windows focus/mouse verification; independent GUI backend test |
| P0 | Local agent mailbox | SQLite WAL peer request-response | Restart/replay, sequence recovery, security scopes |
| P0 | LLM request/response | Explicit compatible HTTP adapter | Provider conformance, credential vault, cost caps |
| P1 | Agent process hardening | Low-priority processes | Job Objects, resource quota and kill-tree |
| P1 | Isolated code/script execution | Not implemented | Per-job Docker/VM sandbox, mounted allowlist paths |
| P1 | Real independent GUI desktops | Not implemented | VM/independent session provisioning, screenshot/input scope |
| P1 | CereX web / device connection | Proposed API only | Authenticated device handshake and mission/status APIs |
| P2 | High reliability orchestration | Basic DAG/timeout | Persistent scheduler checkpoints, retries, dedupe, resume |
| P2 | Model cost/quality governance | Not implemented | Provider-level quota, replayable token/cost ledger |
| P2 | Integrated development autonomy | Not implemented | Code/test/review/deploy permissioned plugin system |

**Do not claim production-ready autonomous GUI control until the independent isolated backend has been built and demonstrated on the actual target OS.**
