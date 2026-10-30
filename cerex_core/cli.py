import argparse
import asyncio
import json
from pathlib import Path
from .bus import Mailbox
from .doctor import snapshot,assessment
from .orchestrator import run,validate


def main():
    parser=argparse.ArgumentParser(prog="cerex-core",description="Headless PC intelligence and multi-agent core (pre-alpha)")
    sub=parser.add_subparsers(dest="command",required=True)
    d=sub.add_parser("doctor",help="Read-only local PC diagnostics")
    d.add_argument("--max-workers",type=int,default=4)
    r=sub.add_parser("run",help="Run a job DAG; defaults to no-network mock")
    r.add_argument("--config",required=True)
    r.add_argument("--root",default=".cerex-runs/local")
    r.add_argument("--live",action="store_true",help="Explicitly enable LLM network requests")
    p=sub.add_parser("plan",help="Validate mission and show DAG (does not execute)")
    p.add_argument("--config",required=True)
    m=sub.add_parser("inbox",help="Display local agent messages")
    m.add_argument("--root",default=".cerex-runs/local")
    m.add_argument("--agent",default="orchestrator")
    api=sub.add_parser("serve",help="Authenticated localhost-only experimental CereX core API")
    api.add_argument("--port",type=int,default=8717)
    api.add_argument("--root",default=".cerex-runs/service")
    api.add_argument("--allow-live",action="store_true")
    args=parser.parse_args()
    if args.command=="serve":
        from .http_api import serve
        serve(port=args.port,root=args.root,allow_live=args.allow_live)
    elif args.command=="doctor":
        s=snapshot()
        print(json.dumps({"snapshot":s,"assessment":assessment(s,args.max_workers)},indent=2))
    elif args.command=="inbox":
        print(json.dumps(Mailbox(Path(args.root)/"messages.sqlite3").pending(args.agent),indent=2))
    else:
        config=json.loads(Path(args.config).read_text(encoding="utf-8"))
        if args.command=="plan":
            jobs=validate(config)
            print(json.dumps({"mission":config.get("mission"),"jobs":[{"id":j["id"],"depends_on":j.get("depends_on",[])} for j in jobs]},indent=2))
        else:
            print(json.dumps(asyncio.run(run(config,args.root,args.live)),indent=2,ensure_ascii=False))

if __name__ == "__main__":
    main()
