import argparse
import asyncio
import json
from pathlib import Path
from .bus import Mailbox
from .doctor import recommend
from .orchestrator import run, validate
from .sandbox import SandboxPolicy


def main():
    parser = argparse.ArgumentParser(prog="cerex-core", description="CereX local PC agent engine (no model inside)")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="Read-only PC diagnostics and agent-count recommendation")
    d.add_argument("--max-agents", type=int, default=4)
    t = sub.add_parser("tools", help="Show the sandbox tool allowlist resolved on this device")
    r = sub.add_parser("run", help="Run a mission of sandboxed agents")
    r.add_argument("--config", required=True)
    r.add_argument("--root", default=".cerex-runs/local")
    p = sub.add_parser("plan", help="Validate a mission and show the agent DAG (does not execute)")
    p.add_argument("--config", required=True)
    m = sub.add_parser("inbox", help="Display local inter-agent messages")
    m.add_argument("--root", default=".cerex-runs/local")
    m.add_argument("--agent", default="orchestrator")
    api = sub.add_parser("serve", help="Authenticated localhost-only device API (experimental)")
    api.add_argument("--port", type=int, default=8717)
    api.add_argument("--root", default=".cerex-runs/service")
    sub.add_parser("connect", help="Connect this PC to CereX cloud and run missions the owner's LLM sends", add_help=False)
    args, _ = parser.parse_known_args()
    if args.command == "connect":
        from .connect import main as connect_main
        import sys
        return connect_main(sys.argv[2:])
    if args.command == "serve":
        from .http_api import serve
        serve(port=args.port, root=args.root)
    elif args.command == "doctor":
        print(json.dumps(recommend(args.max_agents), indent=2))
    elif args.command == "tools":
        print(json.dumps(SandboxPolicy().describe(), indent=2))
    elif args.command == "inbox":
        print(json.dumps(Mailbox(Path(args.root) / "messages.sqlite3").pending(args.agent), indent=2))
    else:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if args.command == "plan":
            agents = validate(config)
            print(json.dumps({"mission": config.get("id"), "goal": config.get("goal"),
                              "agents": [{"id": a["id"], "role": a.get("role", ""),
                                          "steps": len(a.get("steps", [])),
                                          "depends_on": a.get("depends_on", [])} for a in agents]}, indent=2))
        else:
            print(json.dumps(asyncio.run(run(config, args.root)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
