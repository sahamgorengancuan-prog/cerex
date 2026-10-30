"""CereX remote session: an isolated background display the owner's LLM can view and drive.

A session is a headless X display (Xvfb) with its own window manager, entirely separate from the
user's real desktop (:0). CereX captures frames from it and sends clicks/keys/scrolls/URLs to it —
never to the user's screen. The session exists only after the owner starts the agent with remote
enabled, is visible while running, is fully logged, and is torn down on stop. This is process- and
display-level isolation for convenience, not an OS security boundary.

Linux only for this build (Xvfb/x11vnc/xdotool/ffmpeg). Windows/macOS background sessions need a
separately authorized backend and are not provided here.
"""
import base64
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

DISPLAY_MIN = 90
DISPLAY_MAX = 119
_BROWSERS = ("google-chrome", "chromium", "chromium-browser", "chrome")


class SessionError(RuntimeError):
    pass


def _which(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _free_display():
    for n in range(DISPLAY_MIN, DISPLAY_MAX + 1):
        if not Path(f"/tmp/.X{n}-lock").exists() and not Path(f"/tmp/.X11-unix/X{n}").exists():
            return n
    raise SessionError("no free display slot")


class RemoteSession:
    def __init__(self, workspace, width=1280, height=800):
        if os.name != "posix":
            raise SessionError("remote sessions require Linux in this build")
        for tool in ("Xvfb", "xdotool", "ffmpeg"):
            if not shutil.which(tool):
                raise SessionError(f"missing required tool: {tool}")
        self.width = max(320, min(int(width), 1920))
        self.height = max(240, min(int(height), 1200))
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.display_num = None
        self.display = None
        self.xauth = None
        self.procs = {}
        self.browser = _which(_BROWSERS)
        self.started_at = None

    def _env(self):
        e = {"DISPLAY": self.display, "XAUTHORITY": self.xauth,
             "HOME": str(self.workspace), "PATH": "/usr/local/bin:/usr/bin:/bin"}
        return e

    def start(self):
        if self.display:
            return self.info()
        self.display_num = _free_display()
        self.display = f":{self.display_num}"
        self.xauth = str(self.workspace / f"xauth-{self.display_num}")
        open(self.xauth, "w").close()
        os.chmod(self.xauth, 0o600)
        log = open(self.workspace / "session.log", "ab")
        self.procs["xvfb"] = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", f"{self.width}x{self.height}x24", "-nolisten", "tcp"],
            stdout=log, stderr=log, start_new_session=True,
            env={"XAUTHORITY": self.xauth, "PATH": "/usr/local/bin:/usr/bin:/bin"})
        if not self._wait_display():
            self.stop()
            raise SessionError("display did not come up")
        wm = _which(["fluxbox", "openbox", "twm"])
        if wm:
            self.procs["wm"] = subprocess.Popen([wm], stdout=log, stderr=log, start_new_session=True, env=self._env())
        self.started_at = time.time()
        return self.info()

    def _wait_display(self, timeout=8):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if self.procs["xvfb"].poll() is not None:
                return False
            r = subprocess.run(["xdotool", "getdisplaygeometry"], env=self._env(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return True
            time.sleep(0.25)
        return False

    def info(self):
        return {"display": self.display, "geometry": [self.width, self.height],
                "window_manager": "wm" in self.procs, "browser_available": bool(self.browser),
                "running": self.running(), "started_at": self.started_at}

    def running(self):
        x = self.procs.get("xvfb")
        return bool(x and x.poll() is None)

    def _run(self, args, timeout=10):
        return subprocess.run(args, env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)

    def screenshot(self, max_bytes=3_000_000):
        if not self.running():
            raise SessionError("session not running")
        out = self.workspace / "frame.png"
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab",
             "-video_size", f"{self.width}x{self.height}", "-i", self.display,
             "-frames:v", "1", str(out)],
            env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if r.returncode != 0 or not out.exists():
            raise SessionError("capture failed")
        data = out.read_bytes()
        if len(data) > max_bytes:
            raise SessionError("frame too large")
        return {"format": "png", "width": self.width, "height": self.height,
                "base64": base64.b64encode(data).decode("ascii")}

    def _clamp(self, x, y):
        return max(0, min(int(x), self.width - 1)), max(0, min(int(y), self.height - 1))

    def input(self, action, **kw):
        """Drive the isolated display only. Supported: move, click, double_click, right_click,
        scroll, type, key, open_url."""
        if not self.running():
            raise SessionError("session not running")
        if action in ("move", "click", "double_click", "right_click"):
            if "x" in kw and "y" in kw:
                x, y = self._clamp(kw["x"], kw["y"])
                self._run(["xdotool", "mousemove", "--sync", str(x), str(y)])
            if action == "click":
                self._run(["xdotool", "click", str(int(kw.get("button", 1)))])
            elif action == "double_click":
                self._run(["xdotool", "click", "--repeat", "2", "1"])
            elif action == "right_click":
                self._run(["xdotool", "click", "3"])
            return {"ok": True}
        if action == "scroll":
            direction = "5" if str(kw.get("direction", "down")) == "down" else "4"
            for _ in range(max(1, min(int(kw.get("amount", 3)), 20))):
                self._run(["xdotool", "click", direction])
            return {"ok": True}
        if action == "type":
            text = kw.get("text", "")
            if not isinstance(text, str) or len(text) > 4000:
                raise SessionError("invalid text")
            self._run(["xdotool", "type", "--clearmodifiers", "--", text])
            return {"ok": True}
        if action == "key":
            keyseq = kw.get("keys", "")
            if not isinstance(keyseq, str) or not keyseq or len(keyseq) > 128 or any(c in keyseq for c in " ;|&"):
                raise SessionError("invalid key sequence")
            self._run(["xdotool", "key", "--clearmodifiers", keyseq])
            return {"ok": True}
        if action == "open_url":
            url = kw.get("url", "")
            u = urlsplit(url)
            if u.scheme not in ("http", "https") or not u.netloc:
                raise SessionError("only http/https URLs may be opened")
            if not self.browser:
                raise SessionError("no browser available on this device")
            log = open(self.workspace / "session.log", "ab")
            key = "browser"
            if key in self.procs and self.procs[key].poll() is None:
                # Already open: route new URL to the running browser instance.
                subprocess.Popen([self.browser, "--no-sandbox", url], env=self._env(), stdout=log, stderr=log, start_new_session=True)
            else:
                self.procs[key] = subprocess.Popen(
                    [self.browser, "--no-sandbox", "--no-first-run", "--disable-gpu", "--no-default-browser-check",
                     f"--window-size={self.width},{self.height}", "--window-position=0,0",
                     "--user-data-dir=" + str(self.workspace / "browser"), url],
                    env=self._env(), stdout=log, stderr=log, start_new_session=True)
            return {"ok": True, "opened": url}
        raise SessionError(f"unknown action: {action}")

    def stop(self):
        for name, proc in list(self.procs.items()):
            try:
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        time.sleep(0.5)
        for name, proc in list(self.procs.items()):
            try:
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        self.procs = {}
        running = self.display
        self.display = None
        return {"stopped": running}
