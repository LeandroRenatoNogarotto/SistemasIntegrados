from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from ctypes import wintypes


def resolve_max_workers(configured: object) -> int:
    """Numero de workers derivado dos nucleos da maquina.

    Com "auto" (padrao em config.json) usa max(2, nucleos - 1), evitando
    fixar manualmente um numero magico de threads. Um numero explicito no
    config funciona apenas como override, sempre limitado pelos nucleos.
    """
    cores = os.cpu_count() or 1
    if configured in (None, "auto", "", 0, "0"):
        return max(2, cores - 1)
    try:
        requested = int(configured)
    except (TypeError, ValueError):
        return max(2, cores - 1)
    if requested <= 0:
        return max(2, cores - 1)
    return max(1, min(cores, requested))


def current_rss_bytes() -> int:
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        get_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
        get_memory_info.restype = wintypes.BOOL
        ok = get_memory_info(handle, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else 0

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        return int(usage.ru_maxrss * 1024)
    except Exception:
        return 0


def system_memory() -> tuple[int, int, float]:
    """(usada_bytes, total_bytes, load_percent) da RAM fisica da MAQUINA inteira."""
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        total = int(status.ullTotalPhys)
        return total - int(status.ullAvailPhys), total, float(status.dwMemoryLoad)
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if rest:
                    info[key.strip()] = int(rest.strip().split()[0]) * 1024
        total = info.get("MemTotal", 0)
        used = total - info.get("MemAvailable", 0)
        return used, total, (used / total * 100.0) if total else 0.0
    except Exception:
        return 0, 0, 0.0


def system_cpu_times() -> tuple[int, int]:
    """(idle_ticks, total_ticks) da CPU da maquina; use deltas para calcular CPU%."""
    if os.name == "nt":
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))

        def as_int(value: wintypes.FILETIME) -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        # No Windows, "kernel" ja inclui o idle; total = kernel + user.
        return as_int(idle), as_int(kernel) + as_int(user)
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            fields = [int(v) for v in handle.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return idle, sum(fields)
    except Exception:
        return 0, 0


def machine_cpu_percent(prev: tuple[int, int] | None, curr: tuple[int, int]) -> float | None:
    """CPU% da maquina inteira entre duas leituras de system_cpu_times()."""
    if prev is None:
        return None
    idle_delta = curr[0] - prev[0]
    total_delta = curr[1] - prev[1]
    if total_delta <= 0:
        return None
    return max(0.0, min(100.0, (total_delta - idle_delta) / total_delta * 100.0))


@dataclass
class MetricTimer:
    wall_start: float
    cpu_start: float
    rss_start: int

    @classmethod
    def start(cls) -> "MetricTimer":
        return cls(time.perf_counter(), time.process_time(), current_rss_bytes())

    def stop(self) -> dict[str, float | int]:
        rss_end = current_rss_bytes()
        return {
            "wall_ms": (time.perf_counter() - self.wall_start) * 1000.0,
            "cpu_ms": (time.process_time() - self.cpu_start) * 1000.0,
            "rss_start_bytes": self.rss_start,
            "rss_end_bytes": rss_end,
            "rss_delta_bytes": rss_end - self.rss_start,
        }
