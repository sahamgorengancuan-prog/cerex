import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from cerex_core.bus import Mailbox
from cerex_core.doctor import assessment
from cerex_core.gateway import LLMGateway, GatewayError, parse_agent_reply
from cerex_core.orchestrator import run, validate

HERE=Path(__file__).resolve().parents[1]


def sample(cpu=10,mem=30,free=40,available_gb=10):
    return {"logical_cpu":8,"cpu_used_percent":cpu,
            "ram":{"total":20*1024**3,"available":available_gb*1024**3,"used_percent":mem},
            "disk":{"free_gb":free,"used_percent":50},"battery":None}

class DoctorTests(unittest.TestCase):
    def test_worker_capacity(self):
        self.assertEqual(assessment(sample(),4)["recommended_workers"],4)
        self.assertEqual(assessment(sample(cpu=92),4)["recommended_workers"],0)
        self.assertEqual(assessment(sample(mem=92),4)["recommended_workers"],0)
        self.assertEqual(assessment(sample(free=2),4)["recommended_workers"],0)
        self.assertEqual(assessment(sample(cpu=70),4)["recommended_workers"],1)

    def test_battery_guard(self):
        s=sample(); s["battery"]={"percent":10,"plugged_in":False}
        self.assertEqual(assessment(s,4)["recommended_workers"],0)

class BusTests(unittest.TestCase):
    def test_persistent_peer_messages(self):
        with tempfile.TemporaryDirectory() as td:
            b=Mailbox(Path(td)/"mail.sqlite3")
            x=b.send("architect","reviewer","review_request",{"body":"please review"})
            other=Mailbox(Path(td)/"mail.sqlite3")
            inbox=other.pending("reviewer")
            self.assertEqual(inbox[0]["id"],x)
            self.assertEqual(inbox[0]["payload"]["body"],"please review")
            self.assertEqual(other.ack("reviewer",[x]),1)
            self.assertEqual(b.pending("reviewer"),[])
            self.assertEqual(b.ack("architect",[x]),0)

class ValidationTests(unittest.TestCase):
    def test_invalid_dag(self):
        with self.assertRaisesRegex(ValueError,"cycle"):
            validate({"jobs":[{"id":"a","task":"a","depends_on":["b"]},{"id":"b","task":"b","depends_on":["a"]}]})

    def test_gui_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"GUI"):
            validate({"jobs":[{"id":"gui","task":"click the screen","requires_gui":True}]})

    def test_invalid_job_id(self):
        with self.assertRaisesRegex(ValueError,"ID"):
            validate({"jobs":[{"id":"../breakout","task":"bad"}]})

class GatewayTests(unittest.TestCase):
    def test_insecure_remote_rejected(self):
        with self.assertRaises(GatewayError):
            LLMGateway(base_url="http://example.com/v1")

    def test_mock_never_network(self):
        self.assertIn("MOCK:", LLMGateway(live=False).chat("system","hello"))

    def test_parse_replies(self):
        self.assertEqual(parse_agent_reply('{"reply":"ok","messages":[] }')["reply"],"ok")
        self.assertEqual(parse_agent_reply("plain response")["reply"],"plain response")

class OrchestratorTests(unittest.TestCase):
    def test_interagent_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            config=json.loads((HERE/"configs/mission_interagent_demo.json").read_text())
            outcome=asyncio.run(run(config,td,live=False,detector=lambda:sample()))
            self.assertEqual(outcome["status"],"completed")
            self.assertEqual(outcome["results"]["architect"]["round_trips"],2)
            self.assertEqual(outcome["results"]["architect"]["received_messages"],1)
            self.assertEqual(outcome["results"]["reviewer"]["messages_sent"],1)
            self.assertEqual(outcome["jobs"]["integrator"],"completed")
            events=Mailbox(Path(td)/"messages.sqlite3").pending("orchestrator")
            self.assertEqual(len(events),3)

    def test_pressure_defers(self):
        with tempfile.TemporaryDirectory() as td:
            config={"jobs":[{"id":"alpha","task":"do this"}]}
            outcome=asyncio.run(run(config,td,detector=lambda:sample(cpu=98)))
            self.assertEqual(outcome["status"],"deferred")

if __name__ == "__main__":
    unittest.main()

class HttpApiTests(unittest.TestCase):
    def test_authenticated_loopback_roundtrip(self):
        import threading
        import time
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from cerex_core.http_api import LocalCoreServer
        with tempfile.TemporaryDirectory() as td:
            server=LocalCoreServer(("127.0.0.1",0),"a"*32,td)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            host="http://127.0.0.1:"+str(server.server_address[1])
            try:
                with self.assertRaises(HTTPError) as denied:
                    urlopen(host+"/health",timeout=3)
                self.assertEqual(denied.exception.code,401)
                def call(path,body=None):
                    headers={"Authorization":"Bearer "+"a"*32}
                    if body is not None: headers["Content-Type"]="application/json"
                    req=Request(host+path,headers=headers,method="POST" if body is not None else "GET",
                                data=json.dumps(body).encode() if body is not None else None)
                    with urlopen(req,timeout=5) as res: return res.status,json.loads(res.read())
                self.assertEqual(call("/health")[0],200)
                self.assertEqual(call("/diagnostics")[0],200)
                config={"id":"http_demo","jobs":[{"id":"writer","task":"write a safe outline"}]}
                status,payload=call("/missions",{"mission":config,"live":False})
                self.assertEqual(status,202)
                final=None
                for _ in range(50):
                    status,final=call("/missions/http_demo")
                    if status==200:break
                    time.sleep(0.08)
                self.assertEqual(status,200)
                self.assertEqual(final["jobs"]["writer"],"completed")
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)
