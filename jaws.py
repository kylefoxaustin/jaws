#!/usr/bin/env python3

import argparse
import os
import sys
import resource
import random
import time
import signal
import psutil
import ctypes
import platform
import array
import gc
import re
import threading

class Jaws:
    def __init__(self, percentage, static_mode, chunk_size_mb, intensity):
        self.percentage = percentage
        self.static_mode = static_mode
        self.buffers = []  # List to hold memory chunks
        self.buffer_size = 0
        self.page_size = resource.getpagesize()
        self.libc = None
        self.intensity = intensity  # 1-10 scale
        self.stop_threads = False  # Flag for stopping background threads
        self.threads = []  # List to track running threads

        # Set chunk size (convert MB to bytes)
        self.chunk_size = chunk_size_mb * 1024 * 1024

        # Try to load libc for memory locking
        try:
            if platform.system() == "Linux":
                self.libc = ctypes.CDLL('libc.so.6')
            elif platform.system() == "Darwin":  # macOS
                self.libc = ctypes.CDLL('libc.dylib')
            else:
                print(f"Warning: Unsupported platform for libc: {platform.system()}")
        except Exception as e:
            print(f"Warning: Could not load libc: {e}")

        # Get total system memory
        total_memory = psutil.virtual_memory().total
        self.buffer_size = int(total_memory * (self.percentage / 100))
        # Round down to nearest page size
        self.buffer_size = (self.buffer_size // self.page_size) * self.page_size
        if self.buffer_size == 0:
            print("Error: Calculated buffer size is zero. Increase percentage.")
            sys.exit(1)

        # Calculate number of chunks
        self.num_chunks = self.buffer_size // self.chunk_size
        if self.buffer_size % self.chunk_size > 0:
            self.num_chunks += 1

    def create_buffer(self):
        """Creates and locks memory buffers."""
        try:
            # Try to lock all memory using mlockall if available
            if self.libc and platform.system() == "Linux":
                # MCL_CURRENT locks current memory, MCL_FUTURE locks future allocations
                MCL_CURRENT = 1
                MCL_FUTURE = 2
                try:
                    result = self.libc.mlockall(MCL_CURRENT | MCL_FUTURE)
                    if result != 0:
                        print(f"Warning: mlockall failed. May need to run as root or increase RLIMIT_MEMLOCK.")
                except Exception as e:
                    print(f"Warning: mlockall error: {e}")

            print(f"Allocating approximately {self.buffer_size / (1024*1024):.2f} MB in {self.num_chunks} chunks of {self.chunk_size / (1024*1024):.2f} MB each...")

            # Use array module for memory allocation instead of mmap
            for i in range(self.num_chunks):
                # Calculate this chunk's size
                if i == self.num_chunks - 1 and self.buffer_size % self.chunk_size > 0:
                    # Last chunk might be smaller
                    this_chunk_size = self.buffer_size % self.chunk_size
                else:
                    this_chunk_size = self.chunk_size

                # Calculate number of bytes (rounded to page size)
                num_bytes = (this_chunk_size // self.page_size) * self.page_size

                # Allocate the memory
                try:
                    # Using byte array (type 'B')
                    buf = array.array('B', [0] * num_bytes)
                    self.buffers.append(buf)

                    # Progress indicator
                    sys.stdout.write(f"\rAllocated chunk {i+1}/{self.num_chunks} ({(i+1) * self.chunk_size / (1024*1024):.2f} MB / {self.buffer_size / (1024*1024):.2f} MB)")
                    sys.stdout.flush()

                except Exception as e:
                    print(f"\nError allocating chunk {i+1}: {e}")
                    break

            print(f"\nSuccessfully allocated {len(self.buffers)} chunks totaling approximately {sum(len(buf) for buf in self.buffers) / (1024*1024):.2f} MB")

            # Touch memory pages to ensure they're mapped
            self._touch_pages()

            # Force garbage collection to clean up any temporary objects
            gc.collect()

        except Exception as e:
            print(f"Error creating buffers: {e}")
            sys.exit(1)

    def _touch_pages(self):
        """Touch pages in memory buffers to ensure they're mapped."""
        print("Touching memory pages to ensure they're mapped...")
        total_pages = 0
        total_buf_count = len(self.buffers)

        for i, buf in enumerate(self.buffers):
            # Calculate number of pages in this buffer
            buf_size = len(buf)
            pages_in_buf = (buf_size + self.page_size - 1) // self.page_size

            # Touch first byte of each page
            for page in range(pages_in_buf):
                page_offset = page * self.page_size
                if page_offset < buf_size:
                    buf[page_offset] = 1  # Write a byte
                    total_pages += 1

            # Progress indicator (update every 5% or at the end)
            if (i+1) % max(1, total_buf_count // 20) == 0 or i == total_buf_count - 1:
                progress_pct = ((i+1) / total_buf_count) * 100
                sys.stdout.write(f"\rTouched {total_pages} pages across {i+1}/{total_buf_count} chunks ({progress_pct:.1f}%)")
                sys.stdout.flush()

        print(f"\nFinished touching {total_pages} pages")

    def _keep_memory_active_thread(self, thread_id):
        """Thread function for keeping memory active."""
        # Determine access pattern based on intensity
        accesses_per_cycle = self.intensity * 50  # 50-500 accesses per cycle
        cycle_time = max(0.05, 0.5 - (self.intensity * 0.04))  # 0.1s to 0.5s

        print(f"Background thread {thread_id} started: {accesses_per_cycle} accesses every {cycle_time:.2f}s")

        try:
            while not self.stop_threads:
                # Select a subset of buffers for this cycle
                num_buffers = min(self.intensity * 3, len(self.buffers))
                buffers_to_access = random.sample(range(len(self.buffers)), num_buffers)

                for buf_idx in buffers_to_access:
                    buf = self.buffers[buf_idx]
                    buf_size = len(buf)

                    # Calculate accesses per buffer
                    accesses_this_buffer = accesses_per_cycle // num_buffers

                    # For very high intensity, do sequential accesses instead of random
                    if self.intensity >= 8:
                        # Sequential access pattern (more cache-unfriendly)
                        start_offset = random.randint(0, buf_size - accesses_this_buffer - 1)
                        for i in range(accesses_this_buffer):
                            offset = (start_offset + i) % buf_size
                            buf[offset] = (buf[offset] + 1) % 256
                    else:
                        # Random access pattern
                        for _ in range(accesses_this_buffer):
                            offset = random.randint(0, buf_size - 1)
                            buf[offset] = (buf[offset] + 1) % 256

                time.sleep(cycle_time)

        except Exception as e:
            print(f"Error in memory activity thread {thread_id}: {e}")

    def _bulk_memory_thread(self, thread_id):
        """Thread for accessing large sections of memory at once."""
        if self.intensity < 5:
            return  # Only run this for higher intensities

        # Determine pattern based on intensity
        scan_size_mb = self.intensity * 10  # 50MB - 100MB per scan
        scan_interval = max(0.5, 5.0 - (self.intensity * 0.4))  # 0.5s to 5s

        print(f"Bulk memory thread {thread_id} started: scanning {scan_size_mb}MB every {scan_interval:.2f}s")

        scan_size_bytes = scan_size_mb * 1024 * 1024

        try:
            while not self.stop_threads:
                # Select a random buffer
                buf_idx = random.randint(0, len(self.buffers) - 1)
                buf = self.buffers[buf_idx]
                buf_size = len(buf)

                # If buffer is large enough, scan a section
                if buf_size >= scan_size_bytes:
                    start_offset = random.randint(0, buf_size - scan_size_bytes)
                    end_offset = start_offset + scan_size_bytes

                    # Sum values to force memory access (read)
                    checksum = 0
                    for i in range(start_offset, end_offset, 4096):  # Sample every page
                        checksum += buf[i]

                    # Write back to a few locations
                    for i in range(start_offset, end_offset, self.page_size):
                        buf[i] = checksum % 256
                else:
                    # Smaller buffer, scan whole thing
                    checksum = 0
                    for i in range(0, buf_size, 4096):
                        checksum += buf[i]

                    for i in range(0, buf_size, self.page_size):
                        buf[i] = checksum % 256

                time.sleep(scan_interval)

        except Exception as e:
            print(f"Error in bulk memory thread {thread_id}: {e}")

    def _aggressive_access_pattern(self):
        """Start multiple threads to aggressively access memory."""
        # Determine number of threads based on intensity
        num_threads = max(1, min(self.intensity, 8))  # 1-8 threads

        print(f"Starting {num_threads} memory activity threads with intensity level {self.intensity}...")

        # Start normal activity threads
        for i in range(num_threads):
            thread = threading.Thread(target=self._keep_memory_active_thread, args=(i,))
            thread.daemon = True
            thread.start()
            self.threads.append(thread)

        # Start bulk memory access thread for higher intensities
        if self.intensity >= 5:
            thread = threading.Thread(target=self._bulk_memory_thread, args=(num_threads,))
            thread.daemon = True
            thread.start()
            self.threads.append(thread)

        # Start memory walker thread for highest intensities
        if self.intensity >= 8:
            thread = threading.Thread(target=self._memory_walker_thread)
            thread.daemon = True
            thread.start()
            self.threads.append(thread)

    def _memory_walker_thread(self):
        """Thread that walks sequentially through large portions of memory."""
        try:
            while not self.stop_threads:
                # Select a random large buffer
                large_buffers = [i for i, buf in enumerate(self.buffers) if len(buf) > 50*1024*1024]
                if not large_buffers:
                    large_buffers = list(range(len(self.buffers)))

                buf_idx = random.choice(large_buffers)
                buf = self.buffers[buf_idx]
                buf_size = len(buf)

                # Walk through entire buffer in chunks (stream-like access)
                chunk_size = 1024 * 1024  # 1MB at a time
                for offset in range(0, buf_size, chunk_size):
                    if self.stop_threads:
                        break

                    end = min(offset + chunk_size, buf_size)
                    # Read and modify in a streaming pattern
                    for i in range(offset, end, 4096):
                        if i < buf_size:
                            buf[i] = (buf[i] + 1) % 256

                    # Brief pause to not completely saturate the system
                    time.sleep(0.001)

                # Longer pause between full buffer walks
                time.sleep(0.5)

        except Exception as e:
            print(f"Error in memory walker thread: {e}")

    def random_access(self):
        """Performs random reads and writes to the buffers."""
        if not self.buffers:
            print("Error: No buffers created.")
            return

        print(f"Starting aggressive memory access with intensity level {self.intensity}/10. Press Ctrl+C to exit.")

        # Start background threads for aggressive access
        self._aggressive_access_pattern()

        # Main thread now just monitors and reports statistics
        try:
            while True:
                # Report memory and CPU statistics periodically
                process = psutil.Process(os.getpid())
                mem_info = process.memory_info()
                cpu_percent = process.cpu_percent(interval=1.0)

                print(f"Memory: {mem_info.rss / (1024 * 1024):.2f} MB, CPU: {cpu_percent:.1f}%, Intensity: {self.intensity}/10")

                time.sleep(5)  # Report every 5 seconds

        except KeyboardInterrupt:
            print("\nStopping memory access threads...")
            self.stop_threads = True

            # Wait for threads to finish
            for thread in self.threads:
                thread.join(timeout=2.0)

            print("Memory access stopped.")

    def report_utilization(self):
        """Reports the memory utilization of the process."""
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        print(f"Jaws Memory Utilization: {mem_info.rss / (1024 * 1024):.2f} MB / Requested: {self.buffer_size / (1024 * 1024):.2f} MB")

    def cleanup(self):
        """Releases the allocated memory buffers."""
        try:
            # Stop all background threads
            self.stop_threads = True
            for thread in self.threads:
                thread.join(timeout=2.0)

            # Attempt to unlock memory before releasing
            if self.libc and platform.system() == "Linux":
                try:
                    self.libc.munlockall()
                except Exception as e:
                    print(f"Warning: munlockall failed: {e}")

            # Clear references to buffers
            while self.buffers:
                self.buffers.pop()

            # Force garbage collection
            gc.collect()

            print("Memory buffers released.")
        except Exception as e:
            print(f"Error releasing buffers: {e}")

    def run(self):
        # Set process priority to high to reduce chance of swapping
        try:
            os.nice(-10)  # Lower nice value = higher priority, may require root
        except:
            print("Warning: Could not set high process priority. Try running as root.")

        # Attempt to disable swapping for this process on Linux
        try:
            if platform.system() == "Linux":
                # Adjust OOM score to make process less likely to be killed
                with open('/proc/self/oom_score_adj', 'w') as f:
                    f.write('-1000')
        except:
            print("Warning: Could not adjust OOM score.")

        self.create_buffer()
        self.report_utilization()

        if not self.static_mode:
            self.random_access()
        else:
            print(f"Static buffer created with intensity level {self.intensity}/10. Touching buffer aggressively to prevent swapping.")
            # Even in static mode, use the aggressive threads
            self._aggressive_access_pattern()

            # Just monitor in the main thread
            try:
                while True:
                    process = psutil.Process(os.getpid())
                    mem_info = process.memory_info()
                    cpu_percent = process.cpu_percent(interval=1.0)

                    print(f"Memory: {mem_info.rss / (1024 * 1024):.2f} MB, CPU: {cpu_percent:.1f}%, Intensity: {self.intensity}/10")
                    time.sleep(5)
            except KeyboardInterrupt:
                pass  # Exit gracefully

        self.cleanup()


def signal_handler(sig, frame):
    """Handles Ctrl+C gracefully."""
    print("\nCtrl+C detected. Exiting...")
    if jaws_instance:
        jaws_instance.cleanup()
    sys.exit(0)


def parse_chunk_size(chunk_str):
    """Parse chunk size string into MB value."""
    if not chunk_str:
        return 100  # Default 100MB

    # Match patterns like "512MB", "1GB", "100", etc.
    match = re.match(r'^(\d+)(?:\s*([MGK]B?)?)?$', chunk_str, re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid chunk size format: {chunk_str}")

    value, unit = match.groups()
    value = int(value)

    if not unit or unit.upper().startswith('M'):
        return value  # Already in MB
    elif unit.upper().startswith('G'):
        return value * 1024  # Convert GB to MB
    elif unit.upper().startswith('K'):
        return value / 1024  # Convert KB to MB
    else:
        return value  # No recognized unit, assume MB


jaws_instance = None  # Global instance for signal handler

def main():
    global jaws_instance

    parser = argparse.ArgumentParser(description="Jaws: Memory Consumption Tool",
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-low", action="store_true", help="Consume 30% of total RAM")
    parser.add_argument("-mid", action="store_true", help="Consume 50% of total RAM")
    parser.add_argument("-high", action="store_true", help="Consume 75% of total RAM")
    parser.add_argument("-static", action="store_true", help="Create a static buffer (no random access)")
    parser.add_argument("-chunk", type=str, default="100MB",
                        help="Chunk size for memory allocation (e.g., 100MB, 1GB). Default: 100MB")
    parser.add_argument("-intensity", type=int, default=5, choices=range(1, 11),
                        help="Memory access intensity (1-10). Default: 5")

    args = parser.parse_args()

    if not (args.low or args.mid or args.high):
        print("Error: Must specify one of -low, -mid, or -high.")
        parser.print_help()
        sys.exit(1)

    if args.low:
        percentage = 30
    elif args.mid:
        percentage = 50
    elif args.high:
        percentage = 75
    else: # Should never happen, but good practice
        print("Error: Invalid memory option.")
        sys.exit(1)

    # Parse chunk size
    try:
        chunk_size_mb = parse_chunk_size(args.chunk)
        print(f"Using chunk size: {chunk_size_mb} MB")
    except ValueError as e:
        print(f"Error: {e}")
        print("Using default chunk size of 100MB instead.")
        chunk_size_mb = 100

    # Validate intensity
    intensity = args.intensity
    if intensity < 1 or intensity > 10:
        print(f"Warning: Invalid intensity level {intensity}. Using default level 5.")
        intensity = 5

    jaws_instance = Jaws(percentage, args.static, chunk_size_mb, intensity)
    signal.signal(signal.SIGINT, signal_handler)  # Register signal handler
    jaws_instance.run()


if __name__ == "__main__":
    main()
