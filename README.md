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

## RAM Definitions

- **Free** — Memory not used for anything
- **Reclaimable** — Cached data and buffers that the OS can release under memory pressure
- **Available** — `Free + Reclaimable`; the actual memory available to applications
- **Top Processes** — CPU sampling takes ~1 second for accurate results; GPU memory requires NVIDIA (`nvidia-smi`)
- **macOS RAM** — Top processes use `phys_footprint` via `proc_pid_rusage`, which includes compressed memory and matches Activity Monitor's "Memory" column. Other platforms use RSS (Resident Set Size), which excludes compressed memory and may show lower values than Activity Monitor on macOS.

## License

MIT
