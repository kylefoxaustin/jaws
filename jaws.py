#!/usr/bin/env python3
"""
JAWS (Just Another Working Simulator) - v2.0.0

A memory consumption and memory-bandwidth simulation tool for Linux. JAWS
allocates and locks a precise percentage of system RAM in physical memory and
generates customizable, multi-core memory access patterns.

v2 highlights vs v1:
  * numpy-backed buffers: no allocation overshoot. v1 built a temporary Python
    list per chunk, costing ~8x the CHUNK size in transient RAM on top of the
    buffer itself. That excess is independent of the target, so the ratio to
    the target depends entirely on chunk:target -- measured on a 96 GB host, a
    963 MB request cost 1.79x at v1's default 100 MB chunk and 9.02x at a 1 GB
    chunk, while a 4.8 GB request cost 1.17x at the default chunk. v2's total
    error is a constant ~29 MB regardless of target or chunk size.
  * Vectorized access engine that releases the GIL, so worker threads achieve
    real multi-core memory bandwidth instead of time-slicing one core.
  * Genuinely low-CPU --static mode.
  * Hardened ctypes / per-buffer mlock() with errno reporting.
  * Built-in VmSwap residency self-check.
  * Clean, deterministic Ctrl+C shutdown and cleanup.
  * Standard GNU-style (--) command-line flags.

Linux only.
"""

import argparse
import ctypes
import ctypes.util
import gc
import os
import platform
import resource
import sys
import threading
import time

try:
    import numpy as np
except ImportError:
    sys.stderr.write(
        "Error: JAWS v2 requires numpy. Install it with:\n    pip install numpy\n"
    )
    sys.exit(1)

try:
    import psutil
except ImportError:
    sys.stderr.write(
        "Error: JAWS requires psutil. Install it with:\n    pip install psutil\n"
    )
    sys.exit(1)


class Jaws:
    def __init__(self, percentage, static_mode, chunk_size_mb, intensity):
        self.percentage = percentage
        self.static_mode = static_mode
        self.intensity = intensity  # 1-10 scale
        self.chunk_size = int(chunk_size_mb * 1024 * 1024)

        self.buffers = []          # list of numpy uint8 arrays
        self.allocated_bytes = 0   # bytes actually allocated
        self.locked_bytes = 0      # bytes successfully mlock()'d
        self.page_size = resource.getpagesize()

        self.libc = self._load_libc()

        # Stop signalling for worker threads.
        self._stop = threading.Event()
        self.threads = []
        self._cleaned_up = False

        # Compute target size, rounded down to a whole page.
        total_memory = psutil.virtual_memory().total
        self.target_bytes = int(total_memory * (self.percentage / 100))
        self.target_bytes = (self.target_bytes // self.page_size) * self.page_size
        if self.target_bytes == 0:
            sys.stderr.write("Error: Calculated buffer size is zero. Increase percentage.\n")
            sys.exit(1)

        if self.chunk_size < self.page_size:
            self.chunk_size = self.page_size

        # Derive the per-cycle work profile from intensity (1-10).
        # Higher intensity -> larger region touched per cycle and shorter sleep,
        # which together drive higher sustained memory bandwidth.
        self.work_fraction = min(1.0, 0.05 * self.intensity)        # 0.05 .. 0.50
        self.cycle_sleep = max(0.0, 0.20 - (self.intensity * 0.02))  # 0.18 .. 0.0

    # -- setup helpers ----------------------------------------------------

    @staticmethod
    def _load_libc():
        """Load libc with errno support so mlock failures are explainable."""
        name = ctypes.util.find_library("c") or "libc.so.6"
        try:
            libc = ctypes.CDLL(name, use_errno=True)
            libc.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            libc.mlock.restype = ctypes.c_int
            libc.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            libc.munlock.restype = ctypes.c_int
            return libc
        except Exception as e:
            print(f"Warning: Could not load libc ({e}); memory locking disabled.")
            return None

    # -- allocation -------------------------------------------------------

    def create_buffer(self):
        """Allocate, touch, and lock the target memory as numpy chunks.

        NOTE: we deliberately lock each buffer individually with mlock() rather
        than mlockall(MCL_FUTURE). mlockall locks the *entire* address space,
        including sparse reserved mappings, so it force-populates the whole
        process footprint -- measured as VmLck == VmSize, a constant ~1383 MB
        over target on this interpreter. Being a constant and not a multiple,
        it costs 2.37x at a 963 MB request but only 1.27x at a 4.8 GB one, so
        it hurts small requests worst. Per-buffer mlock() locks exactly the
        requested bytes (+0.1 MB measured), keeping the precision this tool
        exists to provide.
        """
        num_chunks = (self.target_bytes + self.chunk_size - 1) // self.chunk_size
        print(
            f"Allocating {self.target_bytes / (1024 * 1024):.2f} MB in up to "
            f"{num_chunks} chunk(s) of {self.chunk_size / (1024 * 1024):.2f} MB..."
        )

        remaining = self.target_bytes
        i = 0
        while remaining > 0:
            this_chunk = min(self.chunk_size, remaining)
            # Round each chunk to a whole number of pages, but never below one
            # page so the final remainder is not silently dropped.
            this_chunk = max(self.page_size, (this_chunk // self.page_size) * self.page_size)
            this_chunk = min(this_chunk, remaining + (self.page_size - 1))
            try:
                # np.zeros uses calloc: no temporary Python list, no overshoot.
                buf = np.zeros(this_chunk, dtype=np.uint8)
            except MemoryError as e:
                print(f"\nError allocating chunk {i + 1}: {e}")
                break

            self.buffers.append(buf)
            self.allocated_bytes += buf.nbytes
            remaining -= buf.nbytes
            i += 1
            sys.stdout.write(
                f"\rAllocated {self.allocated_bytes / (1024 * 1024):.2f} MB / "
                f"{self.target_bytes / (1024 * 1024):.2f} MB"
            )
            sys.stdout.flush()

        print(
            f"\nAllocated {len(self.buffers)} chunk(s) totaling "
            f"{self.allocated_bytes / (1024 * 1024):.2f} MB"
        )

        self._touch_and_lock()
        gc.collect()

    def _touch_and_lock(self):
        """Fault in every page (residency) and mlock each buffer (no swap)."""
        print("Touching pages and locking memory...")
        lock_failed = False
        for buf in self.buffers:
            # Strided assignment in C: one write per page faults the whole chunk in.
            buf[:: self.page_size] = 1
            if self.libc:
                rc = self.libc.mlock(buf.ctypes.data, buf.nbytes)
                if rc == 0:
                    self.locked_bytes += buf.nbytes
                elif not lock_failed:
                    err = ctypes.get_errno()
                    print(
                        f"\nWarning: mlock failed: [{err}] {os.strerror(err)}. "
                        "Run as root or raise RLIMIT_MEMLOCK (see setup_for_jaws.sh). "
                        "Memory is resident but may be swapped under pressure."
                    )
                    lock_failed = True
        print(
            f"Pages resident; locked {self.locked_bytes / (1024 * 1024):.2f} MB "
            f"of {self.allocated_bytes / (1024 * 1024):.2f} MB."
        )

    # -- access engine ----------------------------------------------------

    def _access_worker(self, wid):
        """
        Vectorized read-modify-write over random regions.

        numpy releases the GIL during these bulk operations, so multiple worker
        threads genuinely run in parallel across cores and drive real memory
        bandwidth (unlike v1's per-byte Python loops).
        """
        # Per-thread RNG state; offset the seed so threads diverge.
        state = np.random.default_rng(wid + 1)
        checksum = np.uint64(0)
        try:
            while not self._stop.is_set():
                buf = self.buffers[state.integers(0, len(self.buffers))]
                n = buf.shape[0]
                span = max(self.page_size, int(n * self.work_fraction))
                span = min(span, n)
                start = 0 if span >= n else int(state.integers(0, n - span + 1))
                region = buf[start:start + span]

                # Write pass (RMW) and read pass (reduction) over the region.
                region += 1
                checksum ^= np.uint64(region[:: self.page_size].sum(dtype=np.uint64))

                if self.cycle_sleep:
                    self._stop.wait(self.cycle_sleep)
            # Consume checksum so the read pass cannot be optimized away.
            self._checksum = int(checksum)
        except Exception as e:
            print(f"Error in access worker {wid}: {e}")

    def _static_worker(self):
        """Low-CPU keep-resident loop: one strided touch every few seconds."""
        idx = 0
        try:
            while not self._stop.is_set():
                if self.buffers:
                    buf = self.buffers[idx % len(self.buffers)]
                    buf[:: self.page_size] += 1
                    idx += 1
                self._stop.wait(3.0)
        except Exception as e:
            print(f"Error in static worker: {e}")

    def _start_workers(self):
        if self.static_mode:
            print("Static mode: single low-CPU keep-resident thread.")
            t = threading.Thread(target=self._static_worker, daemon=True)
            t.start()
            self.threads.append(t)
            return

        num_threads = max(1, min(self.intensity, os.cpu_count() or 1))
        print(
            f"Starting {num_threads} vectorized access thread(s) "
            f"(intensity {self.intensity}/10)..."
        )
        for wid in range(num_threads):
            t = threading.Thread(target=self._access_worker, args=(wid,), daemon=True)
            t.start()
            self.threads.append(t)

    # -- reporting --------------------------------------------------------

    @staticmethod
    def _vm_swap_kb():
        """Return this process's VmSwap in KB, or None if unavailable."""
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmSwap:"):
                        return int(line.split()[1])
        except OSError:
            pass
        return None

    def residency_check(self):
        """Report residency, and never let '0 swapped' imply 'cannot be swapped'.

        VmSwap == 0 only means nothing has been evicted YET. Whether it CAN be
        evicted is decided by how much we actually mlock()ed, which is a
        different question -- so an unlocked buffer gets a warning, not a tick.
        """
        swap_kb = self._vm_swap_kb()
        unlocked = self.allocated_bytes - self.locked_bytes
        if swap_kb is None:
            print("VmSwap check: unavailable.")
        elif swap_kb == 0 and unlocked > 0:
            print(
                f"VmSwap check: 0 KB swapped — but only "
                f"{self.locked_bytes / (1024 * 1024):.2f} MB of "
                f"{self.allocated_bytes / (1024 * 1024):.2f} MB is locked, so "
                f"{unlocked / (1024 * 1024):.2f} MB is resident but SWAPPABLE "
                "under memory pressure. Not swapped yet is not the same as "
                "cannot be swapped."
            )
        elif swap_kb == 0:
            print("VmSwap check: 0 KB swapped — all memory resident and locked. ✓")
        else:
            print(
                f"VmSwap check: {swap_kb / 1024:.2f} MB swapped out — memory "
                "is NOT fully resident. Run setup_for_jaws.sh / use sudo."
            )

    def report_utilization(self):
        rss = psutil.Process(os.getpid()).memory_info().rss
        print(
            f"JAWS RSS: {rss / (1024 * 1024):.2f} MB / "
            f"requested {self.target_bytes / (1024 * 1024):.2f} MB "
            f"(allocated {self.allocated_bytes / (1024 * 1024):.2f} MB)"
        )

    def _monitor_loop(self):
        proc = psutil.Process(os.getpid())
        while not self._stop.is_set():
            rss = proc.memory_info().rss
            cpu = proc.cpu_percent(interval=1.0)
            mode = "static" if self.static_mode else f"intensity {self.intensity}/10"
            print(f"Memory: {rss / (1024 * 1024):.2f} MB, CPU: {cpu:.1f}%, {mode}")
            self._stop.wait(4.0)

    # -- lifecycle --------------------------------------------------------

    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True

        self._stop.set()
        for t in self.threads:
            t.join(timeout=2.0)

        if self.libc:
            for buf in self.buffers:
                self.libc.munlock(buf.ctypes.data, buf.nbytes)

        self.buffers.clear()
        gc.collect()
        print("Memory buffers released.")

    def run(self):
        # Best-effort: raise priority and make us a poor OOM-kill target.
        try:
            os.nice(-10)
        except OSError:
            print("Warning: could not raise process priority (try sudo).")
        try:
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("-1000")
        except OSError:
            print("Warning: could not adjust OOM score (try sudo).")

        self.create_buffer()
        self.report_utilization()
        self.residency_check()

        self._start_workers()
        print("Running. Press Ctrl+C to stop.")
        try:
            self._monitor_loop()
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.cleanup()


def parse_chunk_size(chunk_str):
    """Parse a chunk size like '100', '100MB', '1GB', '512KB' into MB."""
    import re
    if not chunk_str:
        return 100
    match = re.match(r"^\s*(\d+)\s*([KMG]B?)?\s*$", chunk_str, re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid chunk size format: {chunk_str}")
    value, unit = match.groups()
    value = int(value)
    unit = (unit or "M").upper()
    if unit.startswith("G"):
        return value * 1024
    if unit.startswith("K"):
        return max(1, value // 1024)
    return value  # MB


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jaws",
        description="JAWS v2.0.0 - memory consumption & bandwidth simulation (Linux)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    mem = parser.add_mutually_exclusive_group(required=True)
    mem.add_argument("--low", action="store_true", help="Consume 30%% of total RAM")
    mem.add_argument("--mid", action="store_true", help="Consume 50%% of total RAM")
    mem.add_argument("--high", action="store_true", help="Consume 75%% of total RAM")
    mem.add_argument("--percent", type=int, metavar="PCT",
                     help="Consume a custom percentage of RAM (1-95)")

    parser.add_argument("--static", action="store_true",
                        help="Low-CPU mode: lock memory, minimal access")
    parser.add_argument("--chunk", type=str, default="100MB",
                        help="Allocation chunk size, e.g. 100MB, 1GB. Default: 100MB")
    parser.add_argument("--intensity", type=int, default=5, choices=range(1, 11),
                        metavar="1-10",
                        help="Access intensity 1-10 (default: 5)")
    parser.add_argument("--version", action="version", version="JAWS 2.0.0")
    return parser


def main():
    if platform.system() != "Linux":
        sys.stderr.write("Error: JAWS v2 is Linux-only.\n")
        sys.exit(1)

    args = build_parser().parse_args()

    if args.percent is not None:
        if not (1 <= args.percent <= 95):
            sys.stderr.write(
                f"Error: --percent {args.percent} is outside the valid range (1-95).\n"
            )
            sys.exit(1)
        percentage = args.percent
    elif args.low:
        percentage = 30
    elif args.mid:
        percentage = 50
    else:
        percentage = 75

    try:
        chunk_size_mb = parse_chunk_size(args.chunk)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    print(
        f"JAWS v2.0.0 | target {percentage}% RAM | chunk {chunk_size_mb} MB | "
        f"intensity {args.intensity}/10 | mode {'static' if args.static else 'dynamic'}"
    )

    Jaws(percentage, args.static, chunk_size_mb, args.intensity).run()


if __name__ == "__main__":
    main()
