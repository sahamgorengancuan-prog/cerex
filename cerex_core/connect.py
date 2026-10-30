"""CereX PC agent: connect this computer to CereX cloud and run missions the owner's LLM sends.

This is the outbound client that turns a PC into a CereX device. It authenticates with a device
token the owner issued in CereX, reports the machine's specs, polls for missions, runs each mission
as sandboxed agents (cerex_core.orchestrator), and posts results back. The tool allowlist and
resource limits are decided HERE, by the device owner — never by the cloud or the commanding LLM.
The cloud can only ask for tools the owner already allowed.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .doctor import recommend
from .orchestrator import run


class Client:
    def __init__(self, base_url, token, allowlist, root, limits, enable_remote=False):
        self.base = base_url.rstrip("/")
        u = urlparse(self.base)
        loop = u.hostname in ("127.0.0.1", "localhost", "::1")
        if u.scheme != "https" and not (u.scheme == "http" and loop):
            raise ValueError("CereX base URL must use HTTPS (except loopback for testing)")
        self.token = token
        self.allowlist = allowlist
        self.limits = limits
        self.enable_remote = enable_remote
        self.session = None
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=data, method=method,
                      headers={"Authorization": "Bearer " + self.token,
                               "Content-Type": "application/json", "Accept": "application/json"})
        with urlopen(req, timeout=40) as res:
            return json.loads(res.read(4 * 1024 * 1024) or b"{}")

    def heartbeat(self):
        try:
            from . import __version__
            diag = recommend(self.limits.get("max_agents", 4))
            # Report the owner-set allowlist and limits so the cloud and the commanding LLM can
            # see exactly which tools this device will accept, before sending any command.
            diag["cerex_agent"] = {"version": __version__, "allowlist": self.allowlist,
                                   "limits": self.limits, "remote": bool(self.enable_remote)}
            return self._call("POST", "/device/heartbeat", {"snapshot": diag})
        except (HTTPError, URLError, ValueError, OSError):
            return None

    def poll(self):
        try:
            return self._call("GET", "/device/poll")
        except (HTTPError, URLError, ValueError, OSError):
            return {"mission": None}

    async def run_mission(self, mission):
        config = dict(mission["config"])
        # The owner's device policy governs execution; cloud-supplied sandbox settings are ignored.
        config["sandbox"] = {"allowlist": self.allowlist, **self.limits}
        root = self.root / mission["id"]
        outcome = await run(config, root)
        payload = {
            "status": outcome.get("status"), "handles": outcome.get("handles", {}),
            "agents": outcome.get("agents", {}), "results": outcome.get("results", {}),
            "failures": outcome.get("failures", {}), "error": outcome.get("reason", ""),
        }
        self._call("POST", "/device/result", {"mission_id": mission["id"], "result": payload})
        return outcome.get("status")

    def session_poll(self):
        try:
            return self._call("GET", "/device/session/poll").get("command")
        except (HTTPError, URLError, ValueError, OSError):
            return None

    def handle_session(self, cmd):
        """Execute one session command against the isolated background display and report back."""
        from .session import RemoteSession, SessionError
        kind, args = cmd.get("kind"), cmd.get("args") or {}
        result = {"ok": True}
        try:
            if not self.enable_remote:
                raise SessionError("remote sessions are disabled on this device")
            if kind == "start":
                self.session = RemoteSession(self.root / "session", args.get("width", 1280), args.get("height", 800))
                result["info"] = self.session.start()
            elif kind == "screenshot":
                if not self.session:
                    raise SessionError("no active session")
                result["frame"] = self.session.screenshot()
            elif kind == "input":
                if not self.session:
                    raise SessionError("no active session")
                action = args.pop("action", "")
                result.update(self.session.input(action, **args))
            elif kind == "stop":
                if self.session:
                    self.session.stop()
                    self.session = None
            else:
                raise SessionError("unknown session command")
        except Exception as exc:
            result = {"ok": False, "error": getattr(exc, "args", [str(exc)])[0] if exc.args else type(exc).__name__}
            if kind == "start" and self.session:
                try:
                    self.session.stop()
                except Exception:
                    pass
                self.session = None
        try:
            self._call("POST", "/device/session/result", {"command_id": cmd["id"], "result": result})
        except (HTTPError, URLError, ValueError, OSError):
            pass

    def serve_once(self):
        self.heartbeat()
        if self.enable_remote:
            cmd = self.session_poll()
            if cmd:
                self.handle_session(cmd)
                return "session:" + cmd.get("kind", "?")
        got = self.poll().get("mission")
        if not got:
            return None
        return asyncio.run(self.run_mission(got))

    def serve_forever(self, interval=2):
        last_hb = 0
        last_mission = 0
        print(f"CereX agent connected to {self.base}; polling for work. Allowed tools: {', '.join(self.allowlist)}"
              + ("; remote sessions ON" if self.enable_remote else ""))
        while True:
            now = time.monotonic()
            if now - last_hb > 30:
                self.heartbeat()
                last_hb = now
            busy = False
            if self.enable_remote:
                cmd = self.session_poll()
                if cmd:
                    busy = True
                    self.handle_session(cmd)
            if now - last_mission > 4:
                last_mission = now
                got = self.poll().get("mission")
                if got:
                    busy = True
                    print(f"Running mission {got['id']} ...")
                    try:
                        status = asyncio.run(self.run_mission(got))
                        print(f"  mission {got['id']}: {status}")
                    except Exception as exc:
                        print(f"  mission {got['id']} failed locally: {type(exc).__name__}")
            if not busy:
                time.sleep(1 if self.enable_remote else interval)


def main(argv=None):
    p = argparse.ArgumentParser(prog="cerex-core connect", description="Run this PC as a CereX device")
    p.add_argument("--base-url", default=os.environ.get("CEREX_BASE_URL", ""))
    p.add_argument("--token", default=os.environ.get("CEREX_DEVICE_TOKEN", ""))
    p.add_argument("--allow", default=os.environ.get("CEREX_ALLOW_TOOLS", "python3"),
                   help="Comma-separated tool allowlist the owner permits (default: python3)")
    p.add_argument("--root", default=os.environ.get("CEREX_AGENT_ROOT", ".cerex-device"))
    p.add_argument("--max-agents", type=int, default=4)
    p.add_argument("--cpu-seconds", type=int, default=120)
    p.add_argument("--memory-mb", type=int, default=1024)
    p.add_argument("--wall-seconds", type=int, default=300)
    p.add_argument("--enable-remote", action="store_true",
                   help="Allow the owner's LLM to open an isolated background screen session on this PC")
    p.add_argument("--once", action="store_true", help="Poll once, run a mission if present, then exit")
    a = p.parse_args(argv)
    if not a.base_url or not a.token:
        p.error("set --base-url and --token (or CEREX_BASE_URL / CEREX_DEVICE_TOKEN)")
    allowlist = [t.strip() for t in a.allow.split(",") if t.strip()]
    limits = {"max_agents": a.max_agents, "cpu_seconds": a.cpu_seconds,
              "memory_mb": a.memory_mb, "wall_seconds": a.wall_seconds}
    client = Client(a.base_url, a.token, allowlist, a.root, limits, enable_remote=a.enable_remote)
    if a.once:
        print(json.dumps({"ran": client.serve_once()}))
    else:
        try:
            client.serve_forever()
        except KeyboardInterrupt:
            print("\nCereX agent stopped.")


if __name__ == "__main__":
    main()
