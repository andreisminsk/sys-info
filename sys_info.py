#!/usr/bin/env python3
"""
Cross-platform system information script (Windows 11, macOS, Linux).
Outputs: Total/Used/Free/Reclaimable RAM (GB and %), CPU load, GPU load, disk space.

Dependencies: psutil (pip install psutil)
GPU detection is best-effort — nvidia-smi, WMI, or /sys/class/drm.
"""

import sys
import os
import re
import json
import platform
import subprocess
import shutil

try:
    import psutil
except ImportError:
    print("ERROR: psutil is required. Install with: pip install psutil")
    sys.exit(1)

# ── Ensure UTF-8 output on Windows ──────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# ── Helpers ──────────────────────────────────────────────────────────────────

def bytes_to_gb(b):
    return b / (1024 ** 3)

def fmt_gb(b):
    return f"{bytes_to_gb(b):.2f}"

def fmt_pct(fraction):
    return f"{fraction * 100:.1f}%"

def run_cmd(cmd, timeout=10):
    """Run a command and return stdout, or None on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=isinstance(cmd, str))
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None

# ── RAM ─────────────────────────────────────────────────────────────────────

# ── Container limits (Linux cgroups) ────────────────────────────────────────

def _read_file(path):
    """Read a small sysfs/cgroup file; return stripped text or None."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _read_int(path):
    val = _read_file(path)
    if val is None:
        return None
    try:
        return int(val.split()[0])
    except (ValueError, IndexError):
        return None


def _read_int_any(paths):
    for p in paths:
        val = _read_int(p)
        if val is not None:
            return val
    return None


def _cgroup_cpu_usage_usec(version):
    """Cumulative CPU time used by this cgroup, in microseconds."""
    if version == 2:
        stat = _read_file("/sys/fs/cgroup/cpu.stat")
        if stat:
            for line in stat.splitlines():
                if line.startswith("usage_usec"):
                    try:
                        return int(line.split()[1])
                    except (ValueError, IndexError):
                        return None
    elif version == 1:
        # cpuacct.usage is in nanoseconds
        ns = _read_int_any([
            "/sys/fs/cgroup/cpuacct/cpuacct.usage",
            "/sys/fs/cgroup/cpu,cpuacct/cpuacct.usage",
        ])
        if ns is not None and ns > 0:
            return ns // 1000
    return None


def _cgroup_mem_used_bytes(version):
    """Current cgroup memory usage excluding inactive file cache
    (same approximation `docker stats` uses)."""
    if version == 2:
        current = _read_int("/sys/fs/cgroup/memory.current")
        stat = _read_file("/sys/fs/cgroup/memory.stat")
        inactive = None
        if stat:
            for line in stat.splitlines():
                if line.startswith("inactive_file"):
                    try:
                        inactive = int(line.split()[1])
                    except (ValueError, IndexError):
                        pass
    elif version == 1:
        current = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        stat = _read_file("/sys/fs/cgroup/memory/memory.stat")
        inactive = None
        if stat:
            for line in stat.splitlines():
                if line.startswith("total_inactive_file"):
                    try:
                        inactive = int(line.split()[1])
                    except (ValueError, IndexError):
                        pass
    else:
        return None
    if current is None:
        return None
    if inactive is not None:
        return max(0, current - inactive)
    return current


def get_cgroup_info():
    """
    Detect container resource limits via cgroups (Linux only).

    Returns dict:
      version          — 2, 1, or None (None = no cgroup limits / not Linux)
      cpu_quota_cores  — CPU allowance from the CFS quota, None if unlimited
      mem_limit_bytes  — memory limit in bytes, None if unlimited
      mem_used_bytes   — current usage excluding inactive file cache
    """
    info = {"version": None, "cpu_quota_cores": None,
            "mem_limit_bytes": None, "mem_used_bytes": None}
    if sys.platform != "linux":
        return info

    # ── cgroup v2 (unified hierarchy) ──
    cpu_max = _read_file("/sys/fs/cgroup/cpu.max")
    if cpu_max is not None:
        info["version"] = 2
        parts = cpu_max.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    info["cpu_quota_cores"] = quota / period
            except (ValueError, ZeroDivisionError):
                pass
        mem_max = _read_file("/sys/fs/cgroup/memory.max")
        if mem_max and mem_max != "max":
            try:
                info["mem_limit_bytes"] = int(mem_max)
            except ValueError:
                pass
        info["mem_used_bytes"] = _cgroup_mem_used_bytes(2)
        return info

    # ── cgroup v1 ──
    quota = _read_int_any([
        "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",
        "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us",
    ])
    period = _read_int_any([
        "/sys/fs/cgroup/cpu/cpu.cfs_period_us",
        "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us",
    ])
    if quota is not None or period is not None:
        info["version"] = 1
        if quota and quota > 0 and period and period > 0:
            info["cpu_quota_cores"] = quota / period
        mem_max = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        # v1 reports ~9.2 EB as a "no limit" sentinel — filter it out
        if mem_max is not None and 0 < mem_max < (1 << 60):
            info["mem_limit_bytes"] = mem_max
        info["mem_used_bytes"] = _cgroup_mem_used_bytes(1)

    return info


def get_ram_info(cgroup=None):
    mem = psutil.virtual_memory()
    total = mem.total
    free = mem.free
    available = mem.available
    used = mem.used

    # Reclaimable = available - free  (cached/buffers that can be released)
    reclaimable = max(0, available - free)

    info = {
        "total_gb": fmt_gb(total),
        "used_gb": fmt_gb(used),
        "used_pct": fmt_pct(used / total) if total else "0%",
        "free_gb": fmt_gb(free),
        "free_pct": fmt_pct(free / total) if total else "0%",
        "reclaimable_gb": fmt_gb(reclaimable),
        "reclaimable_pct": fmt_pct(reclaimable / total) if total else "0%",
        "available_pct": fmt_pct(available / total) if total else "0%",
        "available_gb": fmt_gb(available),
    }

    # Inside a container the cgroup limit is the real ceiling — psutil's
    # "available" reflects the host, not what this container may consume.
    limit = cgroup.get("mem_limit_bytes") if cgroup else None
    if limit and total and limit < total:
        c_used = cgroup.get("mem_used_bytes")
        info["container"] = {
            "limit_gb": fmt_gb(limit),
            "used_gb": fmt_gb(c_used) if c_used is not None else None,
            "used_pct": fmt_pct(c_used / limit) if c_used is not None else None,
            "available_gb": fmt_gb(limit - c_used) if c_used is not None else None,
        }
    return info

# ── CPU ─────────────────────────────────────────────────────────────────────

def get_effective_cpu_count(cgroup=None):
    """CPUs actually available to this process:
    min(sched_getaffinity, cgroup CFS quota), falling back to psutil count."""
    host = psutil.cpu_count(logical=True) or 1
    affinity = None
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        pass
    quota = cgroup.get("cpu_quota_cores") if cgroup else None
    candidates = [c for c in (affinity, quota) if c and c > 0]
    effective = float(min(candidates)) if candidates else float(host)
    return effective, affinity, quota, host


def get_cpu_load(cgroup=None):
    # psutil gives per-core and overall; interval=1 blocks for a 1s sample.
    # Sample cgroup CPU time around the same window (no extra sleep).
    version = cgroup.get("version") if cgroup else None
    u0 = _cgroup_cpu_usage_usec(version) if version else None

    overall = psutil.cpu_percent(interval=1)
    per_core = psutil.cpu_percent(interval=0, percpu=True)
    effective, affinity, quota, host = get_effective_cpu_count(cgroup)

    container_pct = None
    if u0 is not None and quota:
        u1 = _cgroup_cpu_usage_usec(version)
        if u1 is not None and u1 > u0:
            cpu_seconds = (u1 - u0) / 1e6
            container_pct = cpu_seconds / quota * 100

    return {
        "overall_pct": f"{overall:.1f}%",
        "per_core_pct": [f"{c:.1f}%" for c in per_core],
        "core_count_physical": psutil.cpu_count(logical=False),
        "core_count_logical": host,
        "effective_cores": effective,
        "affinity_cores": affinity,
        "quota_cores": quota,
        "container_pct": container_pct,
    }

# ── GPU ──────────────────────────────────────────────────────────────────────

def get_gpu_load_nvidia_smi():
    """Try nvidia-smi for NVIDIA GPU load."""
    if not shutil.which("nvidia-smi"):
        return None

    out = run_cmd([
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits"
    ])
    if not out:
        return None

    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append({
                "name": parts[0],
                "gpu_load_pct": f"{float(parts[1]):.1f}%",
                "vram_used_mb": float(parts[2]),
                "vram_total_mb": float(parts[3]),
            })
    return gpus if gpus else None


def get_gpu_load_windows_wmi():
    """Try WMI for GPU load on Windows (non-NVIDIA)."""
    try:
        import wmi
        c = wmi.WMI(namespace="root\\OpenHardwareMonitor")
        gpu_loads = []
        for sensor in c.Sensor():
            if sensor.SensorType == "Load" and "GPU" in sensor.Name:
                gpu_loads.append({
                    "name": sensor.Name,
                    "gpu_load_pct": f"{sensor.Value:.1f}%",
                })
        if gpu_loads:
            return gpu_loads
    except Exception:
        pass

    # Fallback: try generic WMI for adapter name only
    try:
        import wmi
        c = wmi.WMI()
        gpus = []
        for gpu in c.Win32_VideoController():
            gpus.append({
                "name": gpu.Name or "Unknown GPU",
                "gpu_load_pct": "N/A (no load sensor)",
            })
        return gpus if gpus else None
    except Exception:
        pass
    return None


def get_gpu_load_linux_sysfs():
    """Try /sys/class/drm for AMD/Intel GPU load on Linux."""
    gpus = []
    drm_path = "/sys/class/drm"
    if not os.path.isdir(drm_path):
        return None

    for entry in os.listdir(drm_path):
        card_dir = os.path.join(drm_path, entry, "device")
        if not entry.startswith("card") or not os.path.isdir(card_dir):
            continue

        # Try to get GPU name
        name_path = os.path.join(card_dir, "product")
        gpu_name = "Unknown GPU"
        if os.path.isfile(name_path):
            try:
                with open(name_path) as f:
                    gpu_name = f.read().strip() or gpu_name
            except Exception:
                pass

        # Try AMD GPU busy percent
        busy_path = os.path.join(drm_path, entry, "device/gpu_busy_percent")
        load = "N/A"
        if os.path.isfile(busy_path):
            try:
                with open(busy_path) as f:
                    val = f.read().strip()
                    float(val)  # validate
                    load = f"{val}%"
            except Exception:
                pass

        if load != "N/A" or gpu_name != "Unknown GPU":
            gpus.append({"name": gpu_name, "gpu_load_pct": load})

    return gpus if gpus else None


def get_gpu_load_macos():
    """Get GPU info on macOS via IOKit (no sudo) or powermetrics (sudo)."""
    # Get GPU names from system_profiler
    out = run_cmd(["system_profiler", "SPDisplaysDataType"])
    names = []
    if out:
        names = [n.strip() for n in re.findall(r"Chipset Model:\s*(.+)", out)]

    gpu_load = None
    load_source = "system_profiler"

    # ── Method 1: ioreg (no sudo, no dependencies) ──
    # Try IOGPUDevice first (Intel/older Macs)
    for ioreg_class, patterns in [
        ("IOGPUDevice", [
            (r'"PerformanceStatistics"\s*=\s*\{([^}]+)\}', [
                (r'"GPU Utilization"\s*=\s*(\d+)', "GPU Utilization"),
                (r'"gpu-utilization"\s*=\s*(\d+)', "gpu-utilization"),
                (r'"utilization"\s*=\s*(\d+)', "utilization"),
            ]),
        ]),
        ("AGXAccelerator", [
            (r'"PerformanceStatistics"\s*=\s*\{([^}]+)\}', [
                (r'"Device Utilization %"\s*=\s*(\d+)', "Device Utilization"),
                (r'"Renderer Utilization %"\s*=\s*(\d+)', "Renderer Utilization"),
                (r'"Tiler Utilization %"\s*=\s*(\d+)', "Tiler Utilization"),
                (r'"GPU Utilization"\s*=\s*(\d+)', "GPU Utilization"),
                (r'"gpu-utilization"\s*=\s*(\d+)', "gpu-utilization"),
            ]),
        ]),
    ]:
        if gpu_load is not None:
            break
        ioreg_out = run_cmd(["ioreg", "-r", "-c", ioreg_class, "-d", "3"])
        if not ioreg_out:
            continue
        for dict_pattern, sub_patterns in patterns:
            dict_match = re.search(dict_pattern, ioreg_out)
            if not dict_match:
                continue
            perf_str = dict_match.group(1)
            for val_pattern, label in sub_patterns:
                m = re.search(val_pattern, perf_str)
                if m:
                    gpu_load = float(m.group(1))
                    load_source = f"IOKit/{ioreg_class} ({label})"
                    break
            if gpu_load is not None:
                break

    # ── Method 3: powermetrics (needs sudo) ──
    if gpu_load is None:
        for cmd_prefix in (["sudo", "-n"], []):
            pm_out = run_cmd(
                cmd_prefix + ["powermetrics", "--samplers", "gpu_power", "-i", "1000", "-n", "1"],
                timeout=10,
            )
            if not pm_out:
                continue
            match = re.search(r"GPU Active Ratio:\s*([\d.]+)%", pm_out)
            if match:
                gpu_load = float(match.group(1))
                load_source = "powermetrics" + (" (sudo)" if cmd_prefix else "")
                break

    # Build result
    gpus = []
    for name in (names or ["Unknown GPU"]):
        entry = {"name": name}
        if gpu_load is not None:
            entry["gpu_load_pct"] = f"{gpu_load:.1f}%"
        else:
            entry["gpu_load_pct"] = "N/A (run with sudo for GPU load)"
        gpus.append(entry)

    return gpus, load_source


def get_gpu_load():
    """Best-effort GPU load detection across platforms."""
    # 1. Try nvidia-smi (all platforms)
    result = get_gpu_load_nvidia_smi()
    if result:
        return result, "nvidia-smi"

    # 2. Platform-specific fallbacks
    system = platform.system()
    if system == "Linux":
        result = get_gpu_load_linux_sysfs()
        if result:
            return result, "/sys/class/drm"
    elif system == "Windows":
        result = get_gpu_load_windows_wmi()
        if result:
            return result, "WMI"
    elif system == "Darwin":
        result, source = get_gpu_load_macos()
        return result, source

    return [{"name": "No GPU detected", "gpu_load_pct": "N/A"}], "none"

# ── Top Processes ───────────────────────────────────────────────────────────

def get_top_processes_cpu(n=5, interval=1.0):
    """Get top N processes by CPU usage (1-second sample)."""
    procs = []
    # First pass: initialize cpu_percent
    for p in psutil.process_iter(['pid', 'name']):
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # Sample over interval
    psutil.cpu_percent(interval=interval)
    for p in psutil.process_iter(['pid', 'name']):
        try:
            cpu = p.cpu_percent(interval=None)
            procs.append({'pid': p.pid, 'name': p.info['name'], 'cpu_pct': cpu})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x['cpu_pct'], reverse=True)
    return procs[:n]


def _get_phys_footprint_macos(pid):
    """Get phys_footprint for a process on macOS (matches Activity Monitor).

    Uses proc_pid_rusage with RUSAGE_INFO_V4. The rusage_info_v4 struct has:
      offset 0-15:  ri_uuid (16 bytes)
      offset 16:    ri_user_time
      offset 24:    ri_system_time
      offset 32:    ri_pkg_idle_wkups
      offset 40:    ri_pkg_nonidle_wkups
      offset 48:    ri_pageins
      offset 56:    ri_wired_size
      offset 64:    ri_resident_size
      offset 72:    ri_phys_footprint  <-- this is what Activity Monitor shows
    phys_footprint includes compressed memory, matching Activity Monitor.
    """
    try:
        import ctypes
        import ctypes.util
        import struct
        libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        # Set argtypes to ensure proper calling convention
        libc.proc_pid_rusage.restype = ctypes.c_int
        libc.proc_pid_rusage.argtypes = [
            ctypes.c_int,        # pid
            ctypes.c_int,        # flavor
            ctypes.POINTER(ctypes.c_char),  # buffer
        ]
        buf = ctypes.create_string_buffer(512)
        # RUSAGE_INFO_V4 = 4; returns 0 on success
        ret = libc.proc_pid_rusage(pid, 4, buf)
        if ret == 0:
            phys_footprint = struct.unpack_from('Q', buf, 72)[0]
            if phys_footprint > 0:
                return phys_footprint
    except Exception:
        pass
    return None


def get_top_processes_ram(n=5, cgroup=None):
    """Get top N processes by RAM usage.

    On macOS, uses phys_footprint (includes compressed memory) to match
    Activity Monitor. On other platforms, uses RSS via psutil.
    Percentages are relative to the container memory limit when set.
    """
    procs = []
    total_mem = psutil.virtual_memory().total
    limit = cgroup.get("mem_limit_bytes") if cgroup else None
    if limit and total_mem and limit < total_mem:
        total_mem = limit
    use_footprint = sys.platform == "darwin"

    for p in psutil.process_iter(['pid', 'name']):
        try:
            mem_mb = None
            mem_pct = None

            if use_footprint:
                footprint = _get_phys_footprint_macos(p.pid)
                if footprint is not None and footprint > 0:
                    mem_mb = footprint / (1024 * 1024)
                    mem_pct = (footprint / total_mem) * 100

            if mem_mb is None:
                rss = p.memory_info().rss
                mem_pct = (rss / total_mem) * 100 if total_mem else 0.0
                mem_mb = rss / (1024 * 1024)

            procs.append({'pid': p.pid, 'name': p.info['name'], 'mem_pct': mem_pct,
                         'mem_mb': mem_mb})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x['mem_pct'], reverse=True)
    return procs[:n]


def get_top_processes_gpu(n=5):
    """Get top N processes by GPU usage (NVIDIA only via nvidia-smi)."""
    if not shutil.which("nvidia-smi"):
        return None

    out = run_cmd([
        "nvidia-smi",
        "--query-compute-apps=pid,gpu_uuid,used_gpu_memory",
        "--format=csv,noheader,nounits"
    ])
    if not out:
        return None

    procs = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                pid = int(parts[0])
                # Get process name from pid
                name = "unknown"
                try:
                    name = psutil.Process(pid).name()
                except Exception:
                    pass
                procs.append({
                    'pid': pid,
                    'name': name,
                    'gpu_mem_mb': float(parts[2]),
                })
            except (ValueError, IndexError):
                pass

    procs.sort(key=lambda x: x.get('gpu_mem_mb', 0), reverse=True)
    return procs[:n] if procs else None


# ── Disk ─────────────────────────────────────────────────────────────────────

def get_disk_info():
    """Get disk space info for all mounted volumes."""
    disks = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": fmt_gb(usage.total),
                "used_gb": fmt_gb(usage.used),
                "used_pct": fmt_pct(usage.used / usage.total) if usage.total else "0%",
                "free_gb": fmt_gb(usage.free),
                "free_pct": fmt_pct(usage.free / usage.total) if usage.total else "0%",
            })
        except (PermissionError, OSError):
            # Skip volumes we can't read (e.g. unmounted, restricted)
            continue
    return disks


# ── Display ─────────────────────────────────────────────────────────────────

def collect_all():
    """Gather all metrics into one dict (shared by text and --json output)."""
    cgroup = get_cgroup_info()
    gpus, gpu_method = get_gpu_load()
    has_limits = bool(cgroup["cpu_quota_cores"] or cgroup["mem_limit_bytes"])
    return {
        "platform": f"{platform.system()} {platform.release()}",
        "host": platform.node(),
        "python": platform.python_version(),
        "container": {
            "cgroup_version": cgroup["version"],
            "cpu_quota_cores": cgroup["cpu_quota_cores"],
            "mem_limit_gb": fmt_gb(cgroup["mem_limit_bytes"]) if cgroup["mem_limit_bytes"] else None,
        } if has_limits else None,
        "ram": get_ram_info(cgroup),
        "cpu": get_cpu_load(cgroup),
        "gpus": gpus,
        "gpu_source": gpu_method,
        "disks": get_disk_info(),
        "top_cpu": get_top_processes_cpu(n=5),
        "top_ram": get_top_processes_ram(n=5, cgroup=cgroup),
        "top_gpu": get_top_processes_gpu(n=5),
    }


def display_all(data):
    print("=" * 60)
    print(f"  System Info — {data['platform']}")
    print(f"  Host: {data['host']} | Python {data['python']}")
    if data.get("container"):
        c = data["container"]
        bits = []
        if c["cpu_quota_cores"]:
            bits.append(f"CPU quota {c['cpu_quota_cores']:g}")
        if c["mem_limit_gb"]:
            bits.append(f"RAM limit {c['mem_limit_gb']} GB")
        print(f"  Container: cgroup v{c['cgroup_version']} — {', '.join(bits)}")
    print("=" * 60)

    # RAM
    ram = data["ram"]
    in_container = ram.get("container") is not None
    host_tag = "  (host)" if in_container else ""
    print("\n── Memory (RAM) ──")
    print(f"  Total RAM:         {ram['total_gb']} GB{host_tag}")
    print(f"  Used RAM:          {ram['used_gb']} GB  ({ram['used_pct']})")
    print(f"  Free RAM:          {ram['free_gb']} GB  ({ram['free_pct']})")
    print(f"  Reclaimable RAM:   {ram['reclaimable_gb']} GB  ({ram['reclaimable_pct']})")
    print(f"  Available RAM:     {ram['available_gb']} GB  ({ram['available_pct']}, free + reclaimable)")
    c = ram.get("container")
    if c:
        print(f"  Container Limit:   {c['limit_gb']} GB")
        if c["used_gb"] is not None:
            print(f"  Container Used:    {c['used_gb']} GB  ({c['used_pct']} of limit)")
            print(f"  Container Avail:   {c['available_gb']} GB")

    # CPU
    cpu = data["cpu"]
    print("\n── CPU ──")
    print(f"  CPU Load:          {cpu['overall_pct']}{host_tag}")
    print(f"  Physical Cores:    {cpu['core_count_physical']}")
    print(f"  Logical Cores:     {cpu['core_count_logical']}")
    per_core = cpu["per_core_pct"]
    shown = per_core[:16]
    more = f" … (+{len(per_core) - 16} more)" if len(per_core) > 16 else ""
    print(f"  Per-Core Load:     {', '.join(shown)}{more}")
    if cpu["quota_cores"] or (cpu["affinity_cores"] and cpu["core_count_logical"]
                              and cpu["affinity_cores"] < cpu["core_count_logical"]):
        quota_str = f"{cpu['quota_cores']:g}" if cpu["quota_cores"] else "none"
        aff_str = cpu["affinity_cores"] if cpu["affinity_cores"] is not None else "n/a"
        print(f"  Effective Cores:   {cpu['effective_cores']:g}  (quota: {quota_str}, affinity: {aff_str})")
        if cpu["container_pct"] is not None:
            print(f"  Container Load:    {cpu['container_pct']:.1f}% of {cpu['quota_cores']:g}-core allowance")

    # GPU
    gpus, method = data["gpus"], data["gpu_source"]
    print(f"\n── GPU (source: {method}) ──")
    for i, gpu in enumerate(gpus):
        label = f"  GPU {i}" if len(gpus) > 1 else "  GPU"
        name = gpu.get("name", "Unknown")
        load = gpu.get("gpu_load_pct", "N/A")
        line = f"{label}: {name} — Load: {load}"
        if "vram_total_mb" in gpu:
            vram_pct = f"{gpu['vram_used_mb'] / gpu['vram_total_mb'] * 100:.1f}%"
            line += f" | VRAM: {gpu['vram_used_mb']:.0f}/{gpu['vram_total_mb']:.0f} MB ({vram_pct})"
        print(line)

    # Disk
    print("\n── Disk Space ──")
    for d in data["disks"]:
        label = f"{d['mountpoint']}"
        if d['device'] and d['device'] != d['mountpoint']:
            label = f"{d['device']} ({d['mountpoint']})"
        print(f"  {label}:  {d['used_gb']} / {d['total_gb']} GB used ({d['used_pct']})  |  {d['free_gb']} GB free ({d['free_pct']})")

    # Top processes
    print("\n── Top Processes by CPU ──")
    for p in data["top_cpu"]:
        print(f"  {p['pid']:>7}  {p['cpu_pct']:6.1f}%  {p['name']}")

    print("\n── Top Processes by RAM ──")
    for p in data["top_ram"]:
        print(f"  {p['pid']:>7}  {p['mem_pct']:6.1f}%  {p['mem_mb']:7.1f} MB  {p['name']}")

    if data["top_gpu"]:
        print("\n── Top Processes by GPU Memory ──")
        for p in data["top_gpu"]:
            print(f"  {p['pid']:>7}  {p['gpu_mem_mb']:7.0f} MB  {p['name']}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    data = collect_all()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
    else:
        display_all(data)
