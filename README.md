# CereX PC Agentic Core

**Status: developer prototype, 30 October 2026.** This is the local PC engine for CereX. It is
the "hands" that run on a user's computer. **CereX contains no language model.** The "brain" is
whatever LLM the user already uses (ChatGPT, Claude, …), which connects to CereX through the CereX
cloud connector and commands the PC. CereX diagnoses the machine, spawns the right number of
sandboxed agents, runs the allowlisted tools each agent is told to run, lets agents hand work to
each other, and reports everything back — with a full log of which LLM asked for what.

## The model in one picture

```text
  Your LLM (ChatGPT / Claude / API)         ← the brain, not part of CereX
        │  connector (MCP, outbound)
  CereX Cloud  ── device registry · per-LLM agent ownership · full log
        │  outbound connection from your PC
  CereX PC agent (this engine)
        ├── doctor      read-only CPU/RAM/GPU/disk/battery → recommended agent count
        ├── orchestrator resource-aware DAG of agents, parallel, cancellable
        ├── executor    one process per agent: runs ordered sandboxed tool steps
        ├── sandbox     allowlisted programs only, per-agent workspace, CPU/RAM/time caps
        └── bus         inter-agent messages + shared handoff folder
```

Each agent gets a unique handle (e.g. `CX-demo_pip-01`) so the LLM can address specific agents in
parallel and they can message one another.

## What CereX is and is not

- **No model inside.** There is no API key, no prompt, no inference in this engine. It only
  executes instructions an authorized LLM sends through the connector.
- **No arbitrary shell.** An agent step is a program from the device **allowlist** plus arguments —
  never a shell string. Shells and privilege tools (`sh`, `bash`, `sudo`, `ssh`, …) are refused
  even if an owner lists them.
- **Sandboxed.** Each agent runs in its own workspace with a minimal environment (the host's
  environment and secrets are never passed through) and hard CPU, memory, wall-clock, output and
  file-size limits.
- **Owner in control.** The agent runs only after the PC owner installs and pairs it. It does not
  hide from Task Manager or antivirus and does not auto-start unless the owner enables it. (Pairing,
  the visible status indicator, the global Stop control and remote sessions live in the CereX cloud
  layer, not in this core.)
- **Logged.** Every agent, step, exit code and peer message is recorded.

## Quick start

```bash
python -m pip install psutil          # optional, improves diagnostics
python -m cerex_core doctor --max-agents 4
python -m cerex_core tools            # show the sandbox allowlist on this machine
python -m cerex_core plan --config configs/mission_demo.json
python -m cerex_core run  --config configs/mission_demo.json
python -m cerex_core inbox --agent orchestrator
```

The demo missions use only `python3`, so they run anywhere. A real task (say a 3D render) would
allowlist `blender` and `ffmpeg` and the LLM would send those commands.

## Mission shape

```json
{
  "id": "demo_pipeline",
  "goal": "human/LLM description — never executed",
  "owner_llm": "chatgpt",
  "max_workers": 3,
  "agent_timeout_seconds": 60,
  "sandbox": {"allowlist": ["python3", "blender", "ffmpeg"], "cpu_seconds": 600, "memory_mb": 4096},
  "agents": [
    {"id": "planner", "role": "Write the scene plan",
     "steps": [{"command": ["python3", "plan.py"]}]},
    {"id": "reviewer", "role": "Review once the plan exists",
     "depends_on": ["planner"],
     "steps": [{"command": ["python3", "-c", "..."]}],
     "messages": [{"to": "planner", "kind": "note", "body": "looks good"}]}
  ]
}
```

Agents share files through the `CEREX_SHARED` folder (injected into every step's environment);
each agent's own `CEREX_WORKSPACE` is its private working directory.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Layout

- `cerex_core/doctor.py` — read-only PC snapshot and safe agent-count recommendation
- `cerex_core/sandbox.py` — allowlisted, resource-limited tool execution
- `cerex_core/executor.py` — one agent process: runs its sandboxed steps, uses the bus
- `cerex_core/orchestrator.py` — resource-aware agent DAG, parallelism, cancel, progress
- `cerex_core/bus.py` — durable inter-agent messages
- `cerex_core/http_api.py` — authenticated localhost-only device API (experimental)
- `configs/` — example missions
- `tests/` — offline regression suite
- `docs/` — architecture, integration contract, milestones

## Current limitations

- Remote desktop/GUI control (TeamViewer-style layered sessions) is **not** in this core; it
  requires a separately authorized isolated-session backend and lives in the cloud layer.
- The sandbox is process-level isolation with resource limits — not a full OS/VM security boundary.
  Agents run under the host user's permissions. For untrusted tool execution, add per-agent
  container/VM isolation.
- Pairing, OAuth connector, device registry and the live monitor are in the CereX cloud layer.
