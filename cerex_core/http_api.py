"""Loopback-only experimental bridge. Auth required; not the CereX web control plane."""
import asyncio
import hmac
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .doctor import assessment, snapshot
from .orchestrator import JOB_ID, run, validate


class LocalCoreServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self, address, token, root, allow_live=False):
        if address[0] not in ("127.0.0.1","::1"):
            raise ValueError("Only loopback listen supported")
        if not token or len(token)<24:
            raise ValueError("CEREX_CORE_TOKEN must contain 24+ characters")
        self.token=token
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.allow_live=allow_live
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix="cerex-mission")
        self.missions={}
        self.lock=threading.Lock()
        super().__init__(address,LocalCoreHandler)

    def server_close(self):
        self.executor.shutdown(wait=False,cancel_futures=True)
        super().server_close()


class LocalCoreHandler(BaseHTTPRequestHandler):
    def log_message(self,format,*args):
        # Do not print bearer token, mission prompts, or user identifiers.
        pass

    def send_json(self,code,data):
        result=json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def authenticate(self):
        supplied=self.headers.get("Authorization","")
        if not hmac.compare_digest(supplied,"Bearer "+self.server.token):
            self.send_json(401,{"error":"unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self.authenticate():return
        path=self.path.split("?",1)[0]
        if path=="/health":
            return self.send_json(200,{"service":"cerex-pc-core","status":"ready","scope":"localhost-only"})
        if path=="/diagnostics":
            snap=snapshot()
            return self.send_json(200,{"snapshot":snap,"assessment":assessment(snap,4)})
        matched=re.fullmatch(r"/missions/([A-Za-z][A-Za-z0-9_-]{0,48})",path)
        if matched:
            name=matched.group(1)
            with self.server.lock:
                future=self.server.missions.get(name)
            if future is None:
                return self.send_json(404,{"error":"mission_not_found"})
            if not future.done():
                return self.send_json(202,{"id":name,"status":"running"})
            try:
                return self.send_json(200,future.result())
            except Exception:
                return self.send_json(500,{"id":name,"status":"failed","reason":"internal_error"})
        return self.send_json(404,{"error":"not_found"})

    def do_POST(self):
        if not self.authenticate():return
        if self.path!="/missions":return self.send_json(404,{"error":"not_found"})
        n=int(self.headers.get("Content-Length","0"))
        if not 0<n<=65536:
            return self.send_json(413,{"error":"invalid_size"})
        try:
            payload=json.loads(self.rfile.read(n))
            config=payload.get("mission")
            live=payload.get("live",False)
            if not isinstance(config,dict) or not isinstance(live,bool):
                raise ValueError("Invalid payload")
            validate(config)
            name=config.get("id")
            if not isinstance(name,str) or not JOB_ID.fullmatch(name):
                raise ValueError("Mission requires valid ID")
            if live and not self.server.allow_live:
                return self.send_json(403,{"error":"live_model_disabled"})
        except (ValueError,TypeError,KeyError):
            return self.send_json(400,{"error":"invalid_mission"})
        with self.server.lock:
            if name in self.server.missions:
                return self.send_json(409,{"error":"mission_exists"})
            if sum(1 for f in self.server.missions.values() if not f.done())>=2:
                return self.send_json(429,{"error":"concurrency_limit"})
            future=self.server.executor.submit(lambda: asyncio.run(run(config,self.server.root/name,live=live)))
            self.server.missions[name]=future
        self.send_json(202,{"id":name,"status":"accepted","url":"/missions/"+name})


def serve(host="127.0.0.1",port=8717,root=".cerex-runs/service",allow_live=False):
    token=os.environ.get("CEREX_CORE_TOKEN","")
    srv=LocalCoreServer((host,port),token,root,allow_live=allow_live)
    print(f"CereX Core loopback API listening at http://{host}:{port} (bearer token required)")
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
