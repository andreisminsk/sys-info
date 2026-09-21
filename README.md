# System Info Script

Cross-platform Python script that reports system resource usage on **Windows 11**, **macOS**, and **Linux**.

## Output Metrics

| Category | Metric | Description |
|----------|--------|-------------|
| **RAM** | Total | Total physical memory |
| | Used | Memory in use by processes |
| | Free | Completely unused memory |
| | Reclaimable | Cached/buffers that can be released (`available - free`) |
| | Available | Memory available to apps (`free + reclaimable`) |
| **CPU** | Overall Load | Aggregate CPU utilization |
| | Per-Core Load | Individual core utilization |
| | Core Count | Physical and logical core count |
| **GPU** | Name | GPU model name |
| | GPU Load | GPU compute utilization |
| | VRAM | VRAM usage (NVIDIA only) |
| **Disk** | Total | Total disk capacity per volume |
| | Used | Space used per volume |
| | Free | Space free per volume |
| **Container** | CPU Quota | CFS quota in cores (cgroup v2/v1) |
| | Effective Cores | `min(quota, sched_getaffinity)` |
| | Container CPU Load | CPU time as % of the container's allowance |
| | RAM Limit | cgroup memory limit (when lower than host RAM) |
| | Container Used | cgroup usage excl. inactive file cache (`docker stats`-style) |
| **Top Processes** | By CPU | Top 5 processes by CPU% |
| | By RAM | Top 5 processes by RAM% and MB |
| | By GPU Memory | Top 5 processes by GPU memory (NVIDIA only) |

## Installation

```bash
pip install -r requirements.txt
```

### Optional Dependencies

| Package | Platform | Purpose |
|---------|----------|---------|
| `WMI` | Windows | Better GPU load detection via OpenHardwareMonitor |

```bash
pip install WMI  # Windows only, optional
```

## Usage

```bash
python sys_info.py
```

### JSON Output

```bash
python sys_info.py --json   # machine-readable; includes container limits
```

### Example Output

```
============================================================
  System Info — Windows 10.0.22631
  Host: DESKTOP-ABC | Python 3.12.0
============================================================

── Memory (RAM) ──
  Total RAM:         31.87 GB
  Used RAM:          18.42 GB  (57.8%)
  Free RAM:          5.12 GB   (16.1%)
  Reclaimable RAM:   8.33 GB   (26.1%)
  Available RAM:     13.45 GB  (free + reclaimable)

── CPU ──
  CPU Load:          24.5%
  Physical Cores:    8
  Logical Cores:     16
  Per-Core Load:     12.0%, 8.0%, 45.0%, ...

── GPU (source: nvidia-smi) ──
  GPU: NVIDIA GeForce RTX 4070 — Load: 12.0% | VRAM: 2048/12288 MB (16.7%)

============================================================
```

## GPU Detection Methods

| Platform | Method | Notes |
|----------|--------|-------|
| All | `nvidia-smi` | NVIDIA GPUs — full load + VRAM |
| Linux | `/sys/class/drm` | AMD/Intel — `gpu_busy_percent` |
| Windows | WMI / OpenHardwareMonitor | Requires `pip install WMI` (optional) |
| macOS | `ioreg` (AGXAccelerator) | Apple Silicon GPU load without sudo |
| macOS | `system_profiler` | Fallback for GPU name only |
| macOS | `powermetrics` | Requires `sudo`; most detailed |

## RAM Measurement Methods (Top Processes)

| Platform | Method | Notes |
|----------|--------|-------|
| macOS | `proc_pid_rusage` (RUSAGE_INFO_V4) | `ri_phys_footprint` — includes compressed memory, matches Activity Monitor |
| Windows / Linux | `psutil` RSS | Resident Set Size — physical pages in RAM only |

## Container Awareness (Linux)

In containers (Docker, RunPod, Kubernetes, LXC), `/proc` and `psutil` reflect the
**host**, not the container. The script also reads cgroup limits and reports both views:

| Metric | Source | Notes |
|--------|--------|-------|
| CPU Quota | cgroup v2 `cpu.max` / v1 `cpu.cfs_quota_us` + `cpu.cfs_period_us` | CFS quota expressed in cores |
| Effective Cores | `min(quota, sched_getaffinity)` | Cores actually usable by the process |
| Container CPU Load | cgroup `cpu.stat` / `cpuacct.usage` | CPU time as % of the quota, 1 s sample |
| RAM Limit | cgroup v2 `memory.max` / v1 `memory.limit_in_bytes` | Shown when lower than host RAM |
| Container Used | `memory.current` − `inactive_file` | Matches `docker stats` |

Host metrics remain displayed for reference; container values are prefixed `Container`.
Top-process RAM percentages are computed against the container limit when set.

## RAM Definitions

- **Free** — Memory not used for anything
- **Reclaimable** — Cached data and buffers that the OS can release under memory pressure
- **Available** — `Free + Reclaimable`; the actual memory available to applications
- **Top Processes** — CPU sampling takes ~1 second for accurate results; GPU memory requires NVIDIA (`nvidia-smi`)
- **macOS RAM** — Top processes use `phys_footprint` via `proc_pid_rusage`, which includes compressed memory and matches Activity Monitor's "Memory" column. Other platforms use RSS (Resident Set Size), which excludes compressed memory and may show lower values than Activity Monitor on macOS.

## License

MIT
