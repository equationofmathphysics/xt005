import resource
import threading
import time
from pathlib import Path


stats_lock = threading.Lock()
last_cpu_sample = None
service_stats_lock = threading.Lock()
last_service_cpu_sample = None


def read_meminfo():
    mem = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, value = line.split(":", 1)
                mem[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return None
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", 0)
    used = max(total - available, 0)
    return {"total": total, "used": used, "available": available}


def read_cpu_times():
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            parts = f.readline().split()[1:]
        values = [int(x) for x in parts]
    except (OSError, ValueError):
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle


def process_rss_bytes():
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _service_cgroup_path():
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8") as f:
            for line in f:
                hierarchy, controllers, relative_path = line.rstrip().split(":", 2)
                if hierarchy == "0" and not controllers:
                    return Path("/sys/fs/cgroup") / relative_path.lstrip("/")
    except (OSError, ValueError):
        pass
    return None


def _read_service_cgroup_value(filename):
    cgroup_path = _service_cgroup_path()
    if cgroup_path is None:
        return None
    try:
        return int((cgroup_path / filename).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_service_cgroup_stats():
    cgroup_path = _service_cgroup_path()
    if cgroup_path is None:
        return None
    try:
        fields = {}
        for line in (cgroup_path / "memory.stat").read_text(encoding="utf-8").splitlines():
            key, value = line.split(None, 1)
            fields[key] = int(value)
        return fields
    except (OSError, ValueError):
        return None


def service_process_pss_bytes():
    """Return proportional resident memory for all processes in the service cgroup."""
    cgroup_path = _service_cgroup_path()
    if cgroup_path is None:
        return process_rss_bytes()
    try:
        process_ids = (cgroup_path / "cgroup.procs").read_text(encoding="utf-8").split()
    except OSError:
        return process_rss_bytes()

    total = 0
    sampled = False
    for raw_pid in set(process_ids):
        try:
            lines = Path(f"/proc/{int(raw_pid)}/smaps_rollup").read_text(
                encoding="utf-8"
            ).splitlines()
        except (OSError, ValueError):
            continue
        for line in lines:
            if line.startswith("Pss:"):
                try:
                    total += int(line.split()[1]) * 1024
                    sampled = True
                except (IndexError, ValueError):
                    pass
                break
    return total if sampled else process_rss_bytes()


def service_memory_snapshot():
    """Return process memory separately from reclaimable cgroup file cache."""
    current = _read_service_cgroup_value("memory.current")
    if current is None:
        rss = process_rss_bytes()
        return {
            "memory_bytes": rss,
            "working_set_bytes": rss,
            "process_pss_bytes": rss,
            "file_cache_bytes": 0,
            "reclaimable_bytes": 0,
        }

    stats = _read_service_cgroup_stats() or {}
    process_pss = service_process_pss_bytes()
    reclaimable = stats.get("inactive_file", 0) + stats.get("slab_reclaimable", 0)
    return {
        "memory_bytes": current,
        # Kept for API compatibility. PSS is the useful resident working set;
        # current - inactive_file still misclassifies active file cache as process memory.
        "working_set_bytes": process_pss,
        "process_pss_bytes": process_pss,
        "file_cache_bytes": stats.get("file", 0),
        "reclaimable_bytes": reclaimable,
    }


def service_memory_bytes():
    """Return memory charged to this service, including children and page cache."""
    current = _read_service_cgroup_value("memory.current")
    return current if current is not None else process_rss_bytes()


def service_working_set_bytes():
    """Return the proportional resident set of the service process tree."""
    return service_memory_snapshot()["working_set_bytes"]


def _service_cpu_usage_usec():
    cgroup_path = _service_cgroup_path()
    if cgroup_path is None:
        return None
    try:
        fields = {}
        for line in (cgroup_path / "cpu.stat").read_text(encoding="utf-8").splitlines():
            key, value = line.split(None, 1)
            fields[key] = int(value)
        return fields.get("usage_usec")
    except (OSError, ValueError):
        return None


def current_service_cpu_percent():
    """Return service CPU where 100 percent represents one fully used CPU core."""
    global last_service_cpu_sample
    usage_usec = _service_cpu_usage_usec()
    if usage_usec is None:
        return None
    sampled_at = time.monotonic()
    with service_stats_lock:
        previous = last_service_cpu_sample
        if previous and sampled_at - previous[0] < 1:
            return previous[2]
        last_service_cpu_sample = (sampled_at, usage_usec, 0.0)
    if not previous:
        return 0.0
    previous_at, previous_usage_usec, _ = previous
    elapsed = sampled_at - previous_at
    usage_delta = usage_usec - previous_usage_usec
    if elapsed <= 0 or usage_delta < 0:
        return 0.0
    percent = round(max(0.0, usage_delta / 1_000_000 / elapsed * 100), 1)
    with service_stats_lock:
        last_service_cpu_sample = (sampled_at, usage_usec, percent)
    return percent


def current_cpu_percent():
    global last_cpu_sample
    sample = read_cpu_times()
    if not sample:
        return None
    with stats_lock:
        previous = last_cpu_sample
        last_cpu_sample = (time.time(), sample[0], sample[1])
    if not previous:
        return 0.0
    _, prev_total, prev_idle = previous
    total_delta = sample[0] - prev_total
    idle_delta = sample[1] - prev_idle
    if total_delta <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)
