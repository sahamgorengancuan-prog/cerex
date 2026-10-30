"""Read-only PC intelligence; does not kill processes, alter power plans or settings."""
import ctypes
import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _windows_memory():
    if os.name != "nt":
        return None
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                   ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                   ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                   ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                   ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(stat)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return {"total": stat.ullTotalPhys, "available": stat.ullAvailPhys,
                "used_percent": float(stat.dwMemoryLoad)}
    return None


def _gpus():
    """Best-effort GPU name list via vendor CLIs already on PATH. Never installs anything."""
    names = []
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            import subprocess
            out = subprocess.run([smi, "--query-gpu=name", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=4)
            names += [l.strip() for l in out.stdout.splitlines() if l.strip()]
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return names[:8]


def snapshot(path=None):
    """No privileged reads, process details, credentials, telemetry or network calls."""
    p = Path(path or Path.home())
    disk = shutil.disk_usage(str(p))
    cpu_total = os.cpu_count() or 1
    mem = _windows_memory()
    cpu_percent = None
    battery = None
    process_count = None
    try:
        import psutil
        vm = psutil.virtual_memory()
        mem = {"total": int(vm.total), "available": int(vm.available), "used_percent": round(vm.percent, 1)}
        cpu_percent = round(psutil.cpu_percent(interval=0.15), 1)
        process_count = len(psutil.pids())
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                battery = {"percent": round(bat.percent, 1), "plugged_in": bool(bat.power_plugged)}
        except (OSError, NotImplementedError):
            pass
    except ImportError:
        pass
    disk_pct = round((disk.used / disk.total) * 100, 1) if disk.total else 0
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "os": platform.system(), "release": platform.release(),
            "logical_cpu": cpu_total, "cpu_used_percent": cpu_percent,
            "ram": mem, "disk": {"free_gb": round(disk.free / 1024**3, 2),
                                     "used_percent": disk_pct},
            "battery": battery, "process_count": process_count,
            "gpus": _gpus(),
            "psutil_installed": cpu_percent is not None}


def recommend(max_agents=4, path=None, policy=None):
    """Convenience for the CereX connector: a PC snapshot plus a safe agent-count recommendation.
    The commanding LLM reads this to decide how many agents to create for a task."""
    snap = snapshot(path)
    return {"snapshot": snap, "assessment": assessment(snap, max_agents, policy)}


def assessment(snap, limit=4, policy=None):
    """Configurable, conservative load guard. No system settings are changed."""
    policy = policy or {}
    cpu_cutoff = float(policy.get("max_cpu_percent", 85))
    ram_cutoff = float(policy.get("max_ram_percent", 88))
    disk_floor = float(policy.get("min_disk_free_gb", 3))
    battery_floor = float(policy.get("battery_floor_percent", 25))
    per_worker_gb = float(policy.get("available_ram_gb_per_worker", 1.5))
    cpu_fraction = float(policy.get("logical_cpu_fraction_for_agents", 0.5))
    if not (10 <= cpu_cutoff <= 99 and 10 <= ram_cutoff <= 99 and
            0 <= disk_floor <= 1000 and 0 <= battery_floor <= 100 and
            0.25 <= per_worker_gb <= 256 and 0.1 <= cpu_fraction <= 1.0):
        raise ValueError("Resource policy outside permitted ranges")
    cpu = snap["cpu_used_percent"]
    ram = snap["ram"]
    disk = snap["disk"]
    warnings = []
    if ram is None:
        warnings.append("RAM telemetry unavailable; conservative worker limit")
    if cpu is None:
        warnings.append("CPU load telemetry unavailable; install psutil for adaptive throttling")
    if cpu is not None and cpu >= cpu_cutoff:
        warnings.append("CPU above policy threshold; defer new agents")
    if ram and ram["used_percent"] >= ram_cutoff:
        warnings.append("RAM above policy threshold; defer new agents")
    if disk["free_gb"] < disk_floor:
        warnings.append("Insufficient disk free space; defer new agents")
    battery_low = (snap["battery"] and not snap["battery"]["plugged_in"]
                   and snap["battery"]["percent"] < battery_floor)
    if battery_low:
        warnings.append("Battery below policy threshold; defer new agents")
    block = ((cpu is not None and cpu >= cpu_cutoff) or
             (ram is not None and ram["used_percent"] >= ram_cutoff) or
             disk["free_gb"] < disk_floor or battery_low)
    if block:
        workers = 0
    elif ram is None:
        workers = 1
    else:
        avail_gb = ram["available"] / 1024**3
        cpu_budget = max(1, int(snap["logical_cpu"] * cpu_fraction))
        mem_budget = max(1, int(avail_gb // per_worker_gb))
        workers = min(int(limit), cpu_budget, mem_budget)
        if cpu is not None and cpu >= cpu_cutoff * 0.77:
            workers = min(workers, 1)
    return {"recommended_workers": workers, "max_configured": int(limit),
            "warnings": warnings, "policy": "non-destructive read-only telemetry; no host desktop input",
            "thresholds": {"max_cpu_percent":cpu_cutoff,"max_ram_percent":ram_cutoff,
                           "min_disk_free_gb":disk_floor,"battery_floor_percent":battery_floor,
                           "available_ram_gb_per_worker":per_worker_gb,
                           "logical_cpu_fraction_for_agents":cpu_fraction}}
