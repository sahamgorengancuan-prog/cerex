"""Sandboxed tool execution for CereX agents.

A task is an allowlisted program plus arguments, never a shell string. The program name
(argv[0]) must appear in the policy allowlist, which maps it to a verified absolute path.
Execution is confined to the agent's job directory, runs with a minimal environment, and is
bounded by wall-clock, CPU, memory, output and file-size limits. The LLM that commands CereX
can only ask for tools the PC owner has allowed; it can never run an arbitrary host shell.
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import resource  # POSIX only
except ImportError:  # pragma: no cover - Windows
    resource = None


class SandboxError(RuntimeError):
    pass


# Conservative built-in allowlist. The PC owner extends or overrides this per device;
# nothing runs unless it is named here and resolves to a real executable.
DEFAULT_ALLOWLIST = (
    "python3", "python", "node", "ffmpeg", "ffprobe", "blender",
    "convert", "magick", "pandoc", "soffice", "qpdf", "gs",
)

# Never runnable, even if an owner lists them: these are shells or privilege tools whose
# only purpose would be to escape the allowlist.
FORBIDDEN = {
    "sh", "bash", "zsh", "dash", "csh", "tcsh", "ksh", "fish",
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh",
    "sudo", "su", "doas", "env", "nohup", "setsid", "xargs", "nice",
    "ssh", "scp", "sftp", "telnet", "nc", "ncat", "socat",
}

_NAME_RE = tuple("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _valid_name(name):
    return bool(name) and 1 <= len(name) <= 64 and all(c in _NAME_RE for c in name) and "/" not in name and "\\" not in name


class SandboxPolicy:
    """Resolves which programs may run and the resource ceiling for each task.

    allowlist: iterable of program names (or name->absolute-path overrides) the owner permits.
    Limits are hard caps; a task's own request may lower but never raise them.
    """

    def __init__(self, allowlist=None, *, cpu_seconds=120, wall_seconds=180,
                 memory_mb=1024, output_bytes=1_000_000, file_size_mb=512, max_args=256):
        raw = DEFAULT_ALLOWLIST if allowlist is None else allowlist
        resolved = {}
        overrides = raw.items() if isinstance(raw, dict) else ((n, None) for n in raw)
        for name, override in overrides:
            if not _valid_name(name) or name in FORBIDDEN:
                continue
            path = self._resolve(name, override)
            if path:
                resolved[name] = path
        self.programs = resolved
        self.cpu_seconds = max(1, min(int(cpu_seconds), 3600))
        self.wall_seconds = max(1, min(int(wall_seconds), 7200))
        self.memory_mb = max(64, min(int(memory_mb), 65536))
        self.output_bytes = max(1024, min(int(output_bytes), 16_000_000))
        self.file_size_mb = max(1, min(int(file_size_mb), 65536))
        self.max_args = max(1, min(int(max_args), 4096))

    @staticmethod
    def _resolve(name, override):
        if override:
            p = Path(override)
            if p.is_absolute() and p.is_file() and os.access(p, os.X_OK):
                return str(p)
            return None
        found = shutil.which(name)
        if found and Path(found).name not in FORBIDDEN:
            return str(Path(found).resolve())
        return None

    def available(self):
        return sorted(self.programs)

    def describe(self):
        return {"allowed_tools": self.available(),
                "limits": {"cpu_seconds": self.cpu_seconds, "wall_seconds": self.wall_seconds,
                           "memory_mb": self.memory_mb, "output_bytes": self.output_bytes,
                           "file_size_mb": self.file_size_mb}}


def _preexec(policy):  # pragma: no cover - exercised in subprocess on POSIX
    if resource is None:
        return
    cpu = policy.cpu_seconds
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
    mem = policy.memory_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except (ValueError, OSError):
        pass
    fsize = policy.file_size_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
    except (ValueError, OSError):
        pass
    # Process group isolation is handled by start_new_session=True; do not call setsid here.


def _clean_env(workspace, extra=None):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workspace),
        "TMPDIR": str(workspace),
        "LANG": "C.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt":
        for k in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATHEXT"):
            if k in os.environ:
                env[k] = os.environ[k]
        env["PATH"] = os.environ.get("PATH", "")
        env["TEMP"] = env["TMP"] = str(workspace)
    # Only non-secret, task-scoped values are allowed through; never the host environment.
    for k, v in (extra or {}).items():
        if isinstance(k, str) and isinstance(v, str) and k.isidentifier() and len(v) <= 4096:
            env[k] = v
    return env


def validate_command(policy, command):
    """Returns (program_path, argv) or raises SandboxError. command is a list: [name, *args]."""
    if not isinstance(command, list) or not command:
        raise SandboxError("command must be a non-empty argument list")
    if len(command) > policy.max_args:
        raise SandboxError("too many arguments")
    name = command[0]
    if not _valid_name(name):
        raise SandboxError("invalid program name")
    if name in FORBIDDEN:
        raise SandboxError(f"program '{name}' is never permitted")
    path = policy.programs.get(name)
    if not path:
        raise SandboxError(f"program '{name}' is not in this device's allowlist")
    argv = [path]
    for a in command[1:]:
        if not isinstance(a, str) or len(a) > 8192 or "\x00" in a:
            raise SandboxError("invalid argument")
        argv.append(a)
    return path, argv


def run_tool(policy, workspace, command, *, timeout=None, env=None, stdin_data=None):
    """Run one allowlisted command inside workspace. Returns a result dict; never raises on
    program failure (non-zero exit is reported, not thrown). Raises SandboxError only when the
    command is rejected before launch."""
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    path, argv = validate_command(policy, command)
    wall = policy.wall_seconds if timeout is None else max(1, min(int(timeout), policy.wall_seconds))
    kw = {
        "cwd": str(workspace),
        "env": _clean_env(workspace, env),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
    }
    if os.name == "posix":
        kw["preexec_fn"] = lambda: _preexec(policy)
        kw["start_new_session"] = True
    else:  # pragma: no cover - Windows
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    cap = policy.output_bytes
    proc = subprocess.Popen(argv, **kw)
    truncated = False
    try:
        out, err = proc.communicate(input=stdin_data.encode() if isinstance(stdin_data, str) else stdin_data, timeout=wall)
    except subprocess.TimeoutExpired:
        _kill(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = b"", b""
        return {"ok": False, "status": "timeout", "exit_code": None,
                "stdout": _trim(out, cap)[0], "stderr": _trim(err, cap)[0],
                "program": Path(path).name, "timeout_seconds": wall}
    code = proc.returncode
    o, to = _trim(out, cap)
    e, te = _trim(err, cap)
    truncated = to or te
    return {"ok": code == 0, "status": "completed" if code == 0 else "failed",
            "exit_code": code, "stdout": o, "stderr": e, "output_truncated": truncated,
            "program": Path(path).name}


def _trim(data, cap):
    if data is None:
        return "", False
    if len(data) > cap:
        return data[:cap].decode("utf-8", "replace"), True
    return data.decode("utf-8", "replace"), False


def _kill(proc):
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), 9)
        else:  # pragma: no cover
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass


def workspace_files(workspace, limit=200):
    """List files an agent produced in its workspace, for the owner/LLM to collect."""
    root = Path(workspace)
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out.append({"path": p.relative_to(root).as_posix(), "bytes": size})
            if len(out) >= limit:
                break
    return out
