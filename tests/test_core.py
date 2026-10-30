import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from cerex_core.bus import Mailbox
from cerex_core.doctor import assessment, recommend
from cerex_core.orchestrator import run, validate
from cerex_core.sandbox import SandboxPolicy, run_tool, validate_command, SandboxError

HERE = Path(__file__).resolve().parents[1]


def sample(cpu=10, mem=30, free=40, available_gb=10):
    return {"logical_cpu": 8, "cpu_used_percent": cpu,
            "ram": {"total": 20 * 1024**3, "available": available_gb * 1024**3, "used_percent": mem},
            "disk": {"free_gb": free, "used_percent": 50}, "battery": None}


class DoctorTests(unittest.TestCase):
    def test_agent_capacity(self):
        self.assertEqual(assessment(sample(), 4)["recommended_workers"], 4)
        self.assertEqual(assessment(sample(cpu=92), 4)["recommended_workers"], 0)
        self.assertEqual(assessment(sample(mem=92), 4)["recommended_workers"], 0)
        self.assertEqual(assessment(sample(free=2), 4)["recommended_workers"], 0)
        self.assertEqual(assessment(sample(cpu=70), 4)["recommended_workers"], 1)

    def test_recommend_shape(self):
        r = recommend(4)
        self.assertIn("snapshot", r)
        self.assertIn("recommended_workers", r["assessment"])
        self.assertIn("gpus", r["snapshot"])

    def test_battery_guard(self):
        s = sample()
        s["battery"] = {"percent": 10, "plugged_in": False}
        self.assertEqual(assessment(s, 4)["recommended_workers"], 0)


class SandboxTests(unittest.TestCase):
    def test_shell_and_forbidden_rejected(self):
        pol = SandboxPolicy(["python3", "bash", "sh", "sudo"])
        self.assertIn("python3", pol.available())
        for bad in ("bash", "sh", "sudo"):
            self.assertNotIn(bad, pol.available())

    def test_unlisted_program_rejected(self):
        pol = SandboxPolicy(["python3"])
        with self.assertRaises(SandboxError):
            validate_command(pol, ["ffmpeg", "-version"])

    def test_path_traversal_name_rejected(self):
        pol = SandboxPolicy(["python3"])
        with self.assertRaises(SandboxError):
            validate_command(pol, ["../../bin/sh"])

    def test_runs_in_workspace_and_captures(self):
        pol = SandboxPolicy(["python3"], wall_seconds=20)
        with tempfile.TemporaryDirectory() as td:
            res = run_tool(pol, td, ["python3", "-c", "print('hi'); open('made.txt','w').write('x')"])
            self.assertTrue(res["ok"])
            self.assertIn("hi", res["stdout"])
            self.assertTrue((Path(td) / "made.txt").is_file())

    def test_nonzero_exit_reported_not_raised(self):
        pol = SandboxPolicy(["python3"], wall_seconds=20)
        with tempfile.TemporaryDirectory() as td:
            res = run_tool(pol, td, ["python3", "-c", "import sys;sys.exit(3)"])
            self.assertFalse(res["ok"])
            self.assertEqual(res["exit_code"], 3)

    def test_timeout_enforced(self):
        pol = SandboxPolicy(["python3"], wall_seconds=2)
        with tempfile.TemporaryDirectory() as td:
            res = run_tool(pol, td, ["python3", "-c", "import time;time.sleep(30)"], timeout=1)
            self.assertEqual(res["status"], "timeout")

    def test_host_env_not_leaked(self):
        import os
        os.environ["CEREX_SECRET_PROBE"] = "topsecret"
        try:
            pol = SandboxPolicy(["python3"], wall_seconds=20)
            with tempfile.TemporaryDirectory() as td:
                res = run_tool(pol, td, ["python3", "-c", "import os;print(os.environ.get('CEREX_SECRET_PROBE','ABSENT'))"])
                self.assertIn("ABSENT", res["stdout"])
        finally:
            del os.environ["CEREX_SECRET_PROBE"]


class ValidationTests(unittest.TestCase):
    def test_cycle_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate({"agents": [{"id": "a", "role": "", "steps": [], "depends_on": ["b"]},
                                 {"id": "b", "role": "", "steps": [], "depends_on": ["a"]}]})

    def test_llm_fields_rejected(self):
        with self.assertRaisesRegex(ValueError, "no model"):
            validate({"agents": [{"id": "a", "role": "", "prompt": "do it", "steps": []}]})

    def test_gui_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "GUI"):
            validate({"agents": [{"id": "gui", "role": "", "requires_gui": True, "steps": []}]})

    def test_bad_step_rejected(self):
        with self.assertRaisesRegex(ValueError, "command"):
            validate({"agents": [{"id": "a", "role": "", "steps": [{"label": "x"}]}]})

    def test_invalid_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "ID"):
            validate({"agents": [{"id": "../break", "role": "", "steps": []}]})


class OrchestratorTests(unittest.TestCase):
    def test_mission_with_dependency_and_shared_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            config = json.loads((HERE / "configs/mission_demo.json").read_text())
            outcome = asyncio.run(run(config, td, detector=lambda: sample()))
            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(outcome["agents"], {"planner": "completed", "researcher": "completed", "reviewer": "completed"})
            self.assertTrue(outcome["handles"]["planner"].startswith("CX-"))
            self.assertIn("Reviewed plan", outcome["results"]["reviewer"]["steps"][0]["stdout"])
            self.assertTrue((Path(td) / "shared" / "plan.txt").is_file())
            events = Mailbox(Path(td) / "messages.sqlite3").pending("orchestrator")
            self.assertEqual(len(events), 3)

    def test_interagent_message_delivered(self):
        with tempfile.TemporaryDirectory() as td:
            config = json.loads((HERE / "configs/mission_interagent_demo.json").read_text())
            outcome = asyncio.run(run(config, td, detector=lambda: sample()))
            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(outcome["results"]["architect"]["messages_sent"], 1)
            received = outcome["results"]["reviewer"]["messages_received"]
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["from"], "architect")

    def test_owner_llm_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            config = {"id": "m1", "owner_llm": "chatgpt", "agents": [
                {"id": "a", "role": "noop", "steps": [{"command": ["python3", "-c", "print(1)"]}]}],
                "sandbox": {"allowlist": ["python3"]}}
            outcome = asyncio.run(run(config, td, detector=lambda: sample()))
            self.assertEqual(outcome["owner_llm"], "chatgpt")
            self.assertEqual(outcome["results"]["a"]["owner_llm"], "chatgpt")

    def test_failed_step_skips_dependents(self):
        with tempfile.TemporaryDirectory() as td:
            config = {"id": "m2", "agents": [
                {"id": "boom", "role": "fail", "steps": [{"command": ["python3", "-c", "import sys;sys.exit(1)"]}]},
                {"id": "after", "role": "", "depends_on": ["boom"], "steps": [{"command": ["python3", "-c", "print(1)"]}]}],
                "sandbox": {"allowlist": ["python3"]}}
            outcome = asyncio.run(run(config, td, detector=lambda: sample()))
            self.assertEqual(outcome["status"], "partial")
            self.assertEqual(outcome["agents"]["boom"], "failed")
            self.assertEqual(outcome["agents"]["after"], "skipped")

    def test_pressure_defers(self):
        with tempfile.TemporaryDirectory() as td:
            config = {"id": "m3", "agents": [{"id": "a", "role": "", "steps": [{"command": ["python3", "-c", "print(1)"]}]}],
                      "sandbox": {"allowlist": ["python3"]}}
            outcome = asyncio.run(run(config, td, detector=lambda: sample(cpu=98)))
            self.assertEqual(outcome["status"], "deferred")

    def test_cancel_kills_and_skips(self):
        with tempfile.TemporaryDirectory() as td:
            config = {"id": "m4", "agent_timeout_seconds": 60, "sandbox": {"allowlist": ["python3"], "wall_seconds": 30},
                      "agents": [
                          {"id": "slow", "role": "", "steps": [{"command": ["python3", "-c", "import time;time.sleep(20)"]}]},
                          {"id": "after", "role": "", "depends_on": ["slow"], "steps": [{"command": ["python3", "-c", "print(1)"]}]}]}
            started = time.monotonic()
            updates = []
            outcome = asyncio.run(run(config, td, detector=lambda: sample(),
                                      cancel=lambda: time.monotonic() - started > 0.8,
                                      on_update=lambda u: updates.append(u)))
            self.assertEqual(outcome["status"], "cancelled")
            self.assertEqual(outcome["agents"]["slow"], "cancelled")
            self.assertIn(outcome["agents"]["after"], ("cancelled", "skipped"))
            self.assertLess(time.monotonic() - started, 10)
            self.assertTrue(any(u["statuses"].get("slow") == "running" for u in updates))

    def test_http_api_authenticated_roundtrip(self):
        import threading
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from cerex_core.http_api import LocalCoreServer
        with tempfile.TemporaryDirectory() as td:
            server = LocalCoreServer(("127.0.0.1", 0), "a" * 32, td)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host = "http://127.0.0.1:" + str(server.server_address[1])
            try:
                with self.assertRaises(HTTPError) as denied:
                    urlopen(host + "/health", timeout=3)
                self.assertEqual(denied.exception.code, 401)

                def call(path, body=None):
                    headers = {"Authorization": "Bearer " + "a" * 32}
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                    req = Request(host + path, headers=headers, method="POST" if body is not None else "GET",
                                  data=json.dumps(body).encode() if body is not None else None)
                    with urlopen(req, timeout=5) as res:
                        return res.status, json.loads(res.read())
                self.assertEqual(call("/health")[0], 200)
                self.assertEqual(call("/diagnostics")[0], 200)
                config = {"id": "httpdemo", "sandbox": {"allowlist": ["python3"]},
                          "agents": [{"id": "a", "role": "", "steps": [{"command": ["python3", "-c", "print(1)"]}]}]}
                status, _ = call("/missions", {"mission": config})
                self.assertEqual(status, 202)
                final = None
                for _ in range(60):
                    status, final = call("/missions/httpdemo")
                    if status == 200:
                        break
                    time.sleep(0.1)
                self.assertEqual(status, 200)
                self.assertEqual(final["agents"]["a"], "completed")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()


class SessionTests(unittest.TestCase):
    def test_import_and_guard(self):
        import shutil
        from cerex_core.session import RemoteSession, SessionError
        if shutil.which("Xvfb") and shutil.which("ffmpeg") and shutil.which("xdotool"):
            self.skipTest("display tools present; full session test runs on the device")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SessionError):
                RemoteSession(td).start()
