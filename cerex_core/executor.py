"""CereX agent executor. One agent process runs its ordered steps as sandboxed tool calls.

CereX contains no language model. The commanding LLM (ChatGPT, Claude, ...) connects through the
CereX cloud connector and decides which agents to create and which allowlisted tools each should
run. This executor only carries those instructions out inside a sandbox and reports back.
"""
import argparse
import json
import time
from pathlib import Path
from .bus import Mailbox
from .sandbox import SandboxPolicy, run_tool, workspace_files, SandboxError


def _policy(spec):
    sb = spec.get("sandbox", {})
    return SandboxPolicy(
        sb.get("allowlist"),
        cpu_seconds=sb.get("cpu_seconds", 120),
        wall_seconds=sb.get("wall_seconds", 180),
        memory_mb=sb.get("memory_mb", 1024),
        output_bytes=sb.get("output_bytes", 1_000_000),
        file_size_mb=sb.get("file_size_mb", 512),
    )


def work(spec):
    mailbox = Mailbox(spec["bus"])
    agent = spec["agent_id"]
    job = spec["job"]
    workspace = Path(spec["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    # Shared handoff folder for the whole mission: an agent writes outputs here for its
    # dependents to read. root/workspaces/<agent> -> root/shared
    shared = workspace.parent.parent / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    base_env = {"CEREX_SHARED": str(shared), "CEREX_WORKSPACE": str(workspace), "CEREX_AGENT": agent}
    policy = _policy(spec)
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        steps = []
    results = []
    status = "completed"
    # Drain any peer messages addressed to this agent so the LLM can see them in the result.
    inbox = [{"from": m["sender"], "kind": m["kind"], "payload": m["payload"]} for m in mailbox.pending(agent)]
    mailbox.ack(agent, [m["id"] for m in mailbox.pending(agent)])
    for index, step in enumerate(steps):
        command = step.get("command") if isinstance(step, dict) else None
        label = (step.get("label") if isinstance(step, dict) else None) or f"step{index + 1}"
        started = time.time()
        try:
            step_env = dict(base_env)
            if isinstance(step.get("env"), dict):
                step_env.update(step["env"])
            res = run_tool(policy, workspace, command,
                           timeout=step.get("timeout"),
                           env=step_env,
                           stdin_data=step.get("stdin"))
        except SandboxError as exc:
            res = {"ok": False, "status": "rejected", "error": str(exc)}
        res["label"] = label
        res["seconds"] = round(time.time() - started, 2)
        results.append(res)
        if not res.get("ok"):
            status = "failed"
            if step.get("continue_on_error") is not True:
                break
    # Optional peer messages the LLM asked this agent to send (data only, to known agents).
    sent = 0
    for msg in job.get("messages", []):
        if not isinstance(msg, dict):
            continue
        to = msg.get("to")
        body = msg.get("body", "")
        if to in spec.get("peers", []) and to != agent and isinstance(body, str) and len(body) <= 8000:
            mailbox.send(agent, to, str(msg.get("kind", "handoff"))[:60],
                         {"body": body, "mission_id": spec.get("mission_id")})
            sent += 1
    output = {
        "agent_id": agent, "role": job.get("role", ""), "owner_llm": spec.get("owner_llm"),
        "status": status, "steps": results, "messages_received": inbox, "messages_sent": sent,
        "artifacts": workspace_files(workspace), "allowed_tools": policy.available(),
    }
    (workspace / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    mailbox.send(agent, "orchestrator", "agent_completed", {"agent_id": agent, "status": status})
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()
    result = work(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    print(json.dumps({"agent_id": result["agent_id"], "status": result["status"]}))


if __name__ == "__main__":
    main()
