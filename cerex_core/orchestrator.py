"""Resource-adaptive agent orchestration for CereX.

A mission is a set of agents. Each agent has a role (job description), an ordered list of steps
(each step is one allowlisted, sandboxed tool command), and optional dependencies on other agents.
CereX schedules them in parallel subject to live PC pressure, confines each to its own workspace,
relays peer messages, and records which commanding LLM owns the mission. CereX never contains or
calls a language model itself; the LLM that connects through the CereX cloud connector decides the
agents and their commands.
"""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from .bus import Mailbox
from .doctor import assessment, snapshot

AGENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,48}$")
_LLM_LEFTOVERS = ("provider", "model", "mock_replies", "max_rounds", "prompt", "system")


def validate(config):
    agents = config.get("agents")
    if not isinstance(agents, list) or not agents or len(agents) > 32:
        raise ValueError("agents must be a list of 1-32 items")
    names = set()
    for a in agents:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not AGENT_ID.fullmatch(a["id"]):
            raise ValueError("Invalid agent ID")
        if a["id"] in names:
            raise ValueError("Duplicate agent ID")
        if a["id"] == "orchestrator":
            raise ValueError("Reserved agent ID")
        role = a.get("role", "")
        if not isinstance(role, str) or len(role) > 2000:
            raise ValueError("Invalid agent role")
        if any(k in a for k in _LLM_LEFTOVERS):
            raise ValueError("CereX runs no model; agents carry steps, not prompts")
        if a.get("requires_gui", False):
            raise ValueError("GUI agents require a separately authorized isolated desktop backend")
        steps = a.get("steps", [])
        if not isinstance(steps, list) or len(steps) > 64:
            raise ValueError("steps must be a list of 0-64 items")
        for s in steps:
            if not isinstance(s, dict) or not isinstance(s.get("command"), list) or not s["command"]:
                raise ValueError("each step needs a command argument list")
        if not isinstance(a.get("depends_on", []), list):
            raise ValueError("Invalid dependencies")
        names.add(a["id"])
    deps = {a["id"]: a.get("depends_on", []) for a in agents}
    for k, vs in deps.items():
        if len(set(vs)) != len(vs) or k in vs or any(v not in names for v in vs):
            raise ValueError("Invalid or missing agent dependency")
    visited = set()

    def visit(k, stack):
        if k in stack:
            raise ValueError("Dependency cycle")
        if k in visited:
            return
        for v in deps[k]:
            visit(v, stack | {k})
        visited.add(k)
    for k in names:
        visit(k, set())
    return agents


async def run(config, root, detector=None, env=None, cancel=None, on_update=None):
    """Execute a mission's agents. detector overrides PC telemetry (tests); cancel() kills active
    agents; on_update(statuses) streams progress; env overrides the worker process environment."""
    agents = validate(config)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    mailbox = Mailbox(root / "messages.sqlite3")
    detector = detector or snapshot
    worker_limit = int(config.get("max_workers", 3))
    if worker_limit < 1 or worker_limit > 16:
        raise ValueError("max_workers must be 1-16")
    mission_id = config.get("id", "mission")
    if not isinstance(mission_id, str) or not AGENT_ID.fullmatch(mission_id):
        raise ValueError("Invalid mission ID")
    owner_llm = str(config.get("owner_llm", "unknown"))[:64]
    resource = assessment(detector(), worker_limit, config.get("resource_policy", {}))
    if resource["recommended_workers"] == 0:
        return {"status": "deferred", "resource": resource, "agents": {}, "mission_id": mission_id,
                "owner_llm": owner_llm, "reason": "PC resource-pressure guard"}
    ids = [a["id"] for a in agents]
    # Unique, LLM-addressable handle per agent: CX-<mission>-<NN>.
    handles = {a["id"]: f"CX-{mission_id[:8]}-{i + 1:02d}" for i, a in enumerate(agents)}
    statuses = {i: "pending" for i in ids}
    results = {}
    failures = {}
    active = {}
    procs = {}
    cancelled = False

    def notify():
        if on_update:
            try:
                on_update({"statuses": dict(statuses), "handles": dict(handles)})
            except Exception:
                pass

    async def spawn(agent):
        name = agent["id"]
        directory = root / "workspaces" / name
        directory.mkdir(parents=True, exist_ok=True)
        spec = {
            "mission_id": mission_id, "owner_llm": owner_llm, "agent_id": name,
            "job": agent, "bus": str(root / "messages.sqlite3"), "workspace": str(directory),
            "peers": ids, "sandbox": config.get("sandbox", {}),
        }
        spec_file = directory / "spec.json"
        spec_file.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        kw = {"cwd": str(Path(__file__).resolve().parent.parent),
              "stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE}
        if env is not None:
            kw["env"] = dict(env)
        if os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "cerex_core.executor", "--spec", str(spec_file), **kw)
        procs[name] = proc
        statuses[name] = "running"
        notify()
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=int(config.get("agent_timeout_seconds", 300)))
            if cancelled:
                failures[name] = {"error": "cancelled"}
                statuses[name] = "cancelled"
            elif proc.returncode:
                failures[name] = {"error": "agent_crashed", "stderr_tail": err.decode(errors="replace")[-600:]}
                statuses[name] = "failed"
            else:
                results[name] = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                statuses[name] = results[name].get("status", "completed")
                if statuses[name] != "completed":
                    failures[name] = {"error": "agent_reported_failure"}
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            failures[name] = {"error": "timeout"}
            statuses[name] = "failed"
        finally:
            procs.pop(name, None)
            notify()

    while any(s in ("pending", "running") for s in statuses.values()):
        if cancel and not cancelled and cancel():
            cancelled = True
            for p in list(procs.values()):
                if p.returncode is None:
                    try:
                        p.kill()
                    except ProcessLookupError:
                        pass
            for k, v in statuses.items():
                if v == "pending":
                    statuses[k] = "cancelled"
            notify()
            if not active:
                break
        ready = [a for a in agents if statuses[a["id"]] == "pending" and all(statuses[d] == "completed" for d in a.get("depends_on", []))]
        blocked = [a for a in agents if statuses[a["id"]] == "pending" and any(statuses[d] in ("failed", "skipped", "cancelled", "deferred") for d in a.get("depends_on", []))]
        for a in blocked:
            statuses[a["id"]] = "skipped"
        if blocked:
            notify()
        if ready and not cancelled and len(active) < resource["recommended_workers"]:
            updated = assessment(detector(), worker_limit, config.get("resource_policy", {}))
            capacity = min(resource["recommended_workers"], updated["recommended_workers"]) - len(active)
            for a in ready[:max(0, capacity)]:
                active[a["id"]] = asyncio.create_task(spawn(a))
        if not active:
            if any(s == "pending" for s in statuses.values()):
                for k, v in statuses.items():
                    if v == "pending":
                        statuses[k] = "deferred"
                notify()
            break
        done, _ = await asyncio.wait(active.values(), return_when=asyncio.FIRST_COMPLETED, timeout=0.5 if cancel else None)
        for k, v in list(active.items()):
            if v in done:
                await v
                del active[k]
    overall = "cancelled" if cancelled else "completed" if all(x == "completed" for x in statuses.values()) else "partial"
    final = {"status": overall, "mission_id": mission_id, "owner_llm": owner_llm, "resource": resource,
             "handles": handles, "agents": statuses, "results": results, "failures": failures,
             "root": str(root)}
    (root / "mission-result.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    return final
