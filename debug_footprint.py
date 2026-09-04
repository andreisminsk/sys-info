#!/usr/bin/env python3
"""Debug: try multiple methods to find phys_footprint on macOS."""
import ctypes
import ctypes.util
import struct
import psutil

libc = ctypes.CDLL(ctypes.util.find_library('c'))

pid = psutil.Process().pid
print(f"Testing with PID {pid} (this process)")

# Method 1: proc_pidinfo with various flavors
print("\n=== proc_pidinfo (various flavors) ===")
for flavor in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]:
    buf = ctypes.create_string_buffer(4096)
    ret = libc.proc_pidinfo(pid, flavor, 0, buf, 4096)
    if ret > 0:
        num_values = min(ret, 256) // 8
        print(f"\n  Flavor {flavor}: returned {ret} bytes")
        for i in range(num_values):
            val = struct.unpack_from('Q', buf, i * 8)[0]
            mb = val / (1024**2)
            gb = val / (1024**3)
            if mb > 1:
                print(f"    offset {i*8:3d}: {val:>20,}  ({mb:>10.1f} MB / {gb:.3f} GB)")

# Method 2: proc_pid_rusage with various flavors
print("\n=== proc_pid_rusage (various flavors) ===")
for rusage_flavor in [0, 1, 2, 3, 4]:
    buf = ctypes.create_string_buffer(4096)
    ret = libc.proc_pid_rusage(pid, rusage_flavor, buf)
    if ret == 0:
        num_values = min(4096, 256) // 8
        print(f"\n  RUSAGE_INFO_V{rusage_flavor}: success")
        for i in range(num_values):
            val = struct.unpack_from('Q', buf, i * 8)[0]
            mb = val / (1024**2)
            gb = val / (1024**3)
            if mb > 1:
                print(f"    offset {i*8:3d}: {val:>20,}  ({mb:>10.1f} MB / {gb:.3f} GB)")

# psutil reference
p = psutil.Process()
print(f"\npsutil RSS: {p.memory_info().rss / (1024**2):.1f} MB")
print(f"psutil VMS: {p.memory_info().vms / (1024**2):.1f} MB")
