"""Resource-adaptive DAG orchestration, separate low-priority OS processes per job."""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from .bus import Mailbox
from .doctor import assessment,snapshot

JOB_ID=re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,48}$")


def validate(config):
    jobs=config.get("jobs")
    if not isinstance(jobs,list) or not jobs or len(jobs)>32:
        raise ValueError("jobs must be a list of 1-32 items")
    names=set()
    for j in jobs:
        if not isinstance(j,dict) or not isinstance(j.get("id"),str) or not JOB_ID.fullmatch(j["id"]):
            raise ValueError("Invalid job ID")
        if j["id"] in names:
            raise ValueError("Duplicate job ID")
        if not isinstance(j.get("task"),str) or not j["task"].strip() or len(j["task"])>10000:
            raise ValueError("Invalid task")
        if j.get("requires_gui",False) or j.get("execute_commands",False):
            raise ValueError("GUI/command jobs not implemented; require approved isolated executor")
        if not isinstance(j.get("depends_on",[]),list):
            raise ValueError("Invalid dependencies")
        names.add(j["id"])
    deps={j["id"]:j.get("depends_on",[]) for j in jobs}
    for k,vs in deps.items():
        if len(set(vs))!=len(vs) or k in vs or any(v not in names for v in vs):
            raise ValueError("Invalid or missing job dependency")
    visited=set()
    def visit(k,stack):
        if k in stack: raise ValueError("Dependency cycle")
        if k in visited: return
        for v in deps[k]: visit(v,stack|{k})
        visited.add(k)
    for k in names: visit(k,set())
    return jobs


async def run(config, root, live=False, detector=None):
    jobs=validate(config)
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    mailbox=Mailbox(root/"messages.sqlite3")
    detector=detector or snapshot
    worker_limit=int(config.get("max_workers",3))
    if worker_limit<1 or worker_limit>16: raise ValueError("max_workers must be 1-16")
    resource=assessment(detector(),worker_limit,config.get("resource_policy",{}))
    if resource["recommended_workers"] == 0:
        return {"status":"deferred","resource":resource,"jobs":{},"reason":"PC resource-pressure guard"}
    ids=[j["id"] for j in jobs]
    statuses={i:"pending" for i in ids}
    results={}
    active={}
    failures={}
    mission_id=config.get("id","mission")
    if not isinstance(mission_id,str) or not JOB_ID.fullmatch(mission_id):
        raise ValueError("Invalid mission ID")
    async def spawn(job):
        name=job["id"]
        directory=root/"workspaces"/name
        directory.mkdir(parents=True,exist_ok=True)
        spec={"mission":str(config.get("mission", ""))[:10000],"mission_id":mission_id,
              "job":job,"bus":str(root/"messages.sqlite3"),"workspace":str(directory),
              "dependencies":{d:results[d]["reply"] for d in job.get("depends_on",[])},
              "provider":config.get("provider",{}),"agents":ids,"live":live}
        spec_file=directory/"spec.json"
        spec_file.write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding="utf-8")
        kw={"cwd":str(Path(__file__).resolve().parent.parent),"stdout":asyncio.subprocess.PIPE,
            "stderr":asyncio.subprocess.PIPE}
        if os.name == "nt":
            kw["creationflags"]=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"BELOW_NORMAL_PRIORITY_CLASS",0)
        proc=await asyncio.create_subprocess_exec(sys.executable,"-m","cerex_core.worker","--spec",str(spec_file),**kw)
        statuses[name]="running"
        try:
            out,err=await asyncio.wait_for(proc.communicate(),timeout=int(config.get("job_timeout_seconds",120)))
            if proc.returncode:
                failures[name] = {"exit_code":proc.returncode,"stderr_tail":err.decode(errors="replace")[-600:]}
                statuses[name]="failed"
            else:
                result_file=directory/"result.json"
                results[name]=json.loads(result_file.read_text(encoding="utf-8"))
                statuses[name]="completed"
        except asyncio.TimeoutError:
            proc.kill(); await proc.communicate()
            failures[name] = {"reason":"timeout"}; statuses[name]="failed"
    while any(s in ("pending","running") for s in statuses.values()):
        ready=[j for j in jobs if statuses[j["id"]]=="pending" and all(statuses[d]=="completed" for d in j.get("depends_on",[]))]
        blocked=[j for j in jobs if statuses[j["id"]]=="pending" and any(statuses[d] in ("failed","skipped") for d in j.get("depends_on",[]))]
        for j in blocked: statuses[j["id"]]="skipped"
        if ready and len(active) < resource["recommended_workers"]:
            updated=assessment(detector(),worker_limit,config.get("resource_policy",{}))
            if updated["recommended_workers"] == 0:
                for j in ready: statuses[j["id"]]="deferred"
                break
            capacity=min(resource["recommended_workers"],updated["recommended_workers"])-len(active)
            for j in ready[:max(0,capacity)]:
                active[j["id"]]=asyncio.create_task(spawn(j))
        if not active:
            if any(s=="pending" for s in statuses.values()):
                for k,v in statuses.items():
                    if v=="pending": statuses[k]="deferred"
            break
        done,_=await asyncio.wait(active.values(),return_when=asyncio.FIRST_COMPLETED)
        for k,v in list(active.items()):
            if v in done:
                await v
                del active[k]
    final={"status":"completed" if all(x=="completed" for x in statuses.values()) else "partial",
           "resource":resource,"jobs":statuses,"results":results,"failures":failures,
           "root":str(root),"mode":"live" if live else "mock"}
    (root/"mission-result.json").write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding="utf-8")
    return final
