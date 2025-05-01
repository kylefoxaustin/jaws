# JAWS: Memory Consumer With Memory Locking

JAWS (Just Another Working Simulator) is a sophisticated memory consumption tool designed to simulate real-world application memory usage patterns. It creates and locks memory buffers in main memory to reliably consume specific percentages of system RAM while generating customizable memory access patterns.

## Purpose

JAWS serves several key purposes:

- **Hardware Testing**: Simulate memory-intensive applications for hardware validation
- **Performance Analysis**: Test system behavior under controlled memory constraints
- **Bandwidth Simulation**: Emulate smaller memory configurations on larger hardware (e.g., make a 64-bit DDR bus behave like a 32-bit DDR bus)
- **Memory Locking**: Guarantee that allocated memory remains in physical RAM without swapping

Unlike simple memory allocators, JAWS ensures memory remains resident in physical RAM through multiple locking mechanisms, active memory access patterns, and system configuration optimizations.

## Features

- **Precise Memory Allocation**: Request exact percentages of system memory
- **Memory Locking**: Prevents the OS from swapping allocated memory to disk
- **Customizable Chunk Size**: Control allocation granularity for reliability vs. speed
- **Adjustable Access Intensity**: Fine-tune memory access patterns from light to extreme
- **Multi-threaded Architecture**: Utilize multiple access patterns concurrently
- **System Configuration**: Automatically adjust system settings for optimal memory locking
- **Graceful Cleanup**: Restore system to original state after completion

## Installation

### Prerequisites

- Python 3.6 or higher
- Root/sudo privileges (required for memory locking)
- Linux operating system (tested on Ubuntu, Debian, and CentOS)

### Required Python Packages

- `psutil`: For system memory information
- Standard library packages: `array`, `ctypes`, `threading`, etc.

Install the required package:

```bash
pip install psutil
```

### Clone the Repository

```bash
git clone https://github.com/yourusername/jaws.git
cd jaws
```

### Make Scripts Executable

```bash
chmod +x jaws.py setup_for_jaws.sh deconstruct_jaws.sh
```

## Usage

JAWS requires elevated privileges to lock memory and adjust system settings. The provided setup script handles all necessary configurations.

### Basic Usage

```bash
sudo ./setup_for_jaws.sh -mid
```

This command:
1. Creates a backup of your system settings
2. Configures memory locking parameters
3. Runs JAWS to consume 50% of system memory

### Command Line Options

JAWS supports the following options:

#### Memory Percentage Options (Required - choose one)

- `-low`: Consume 30% of total system RAM
- `-mid`: Consume 50% of total system RAM
- `-high`: Consume 75% of total system RAM
- `-percent PCT`: Consume a custom percentage (1-95%) of total system RAM

#### Memory Access Options

- `-static`: Create a static buffer without random access patterns (default: off)
- `-chunk=SIZE`: Specify chunk size for memory allocation (e.g., 100MB, 1GB, etc.)
- `-intensity=LEVEL`: Set memory access intensity from 1-10 (default: 5)

### Examples

```bash
# Consume 30% of RAM with default settings
sudo ./setup_for_jaws.sh -low

# Consume 75% of RAM with large chunks and high intensity
sudo ./setup_for_jaws.sh -high -chunk=1GB -intensity=8

# Consume 50% of RAM with a static buffer (minimal CPU usage)
sudo ./setup_for_jaws.sh -mid -static

# Consume a custom 42% of RAM with moderate intensity
sudo ./setup_for_jaws.sh -percent 42 -intensity=6

# Consume a custom 20% of RAM with large chunks
sudo ./jaws.py -percent 20 -chunk=512MB
```

## Understanding JAWS Options

### Memory Percentage (-low, -mid, -high, -percent)

These options control what percentage of your total system RAM JAWS will allocate and lock:

- `-low`: 30% - Useful for light testing without significantly impacting system performance
- `-mid`: 50% - Balanced option for most testing scenarios
- `-high`: 75% - Heavy memory pressure, may impact other applications
- `-percent PCT`: Specify a custom percentage from 1% to 95% of system memory

The `-percent` option gives you precise control over memory consumption. For example:
```bash
sudo ./jaws.py -percent 42
```
This will consume exactly 42% of your system's RAM.

### Chunk Size (-chunk)

Controls how memory is allocated internally:

- Smaller chunks (e.g., 100MB): More reliable allocation but slower startup
- Larger chunks (e.g., 1GB): Faster allocation but higher risk of allocation failures

The optimal chunk size depends on your system and total allocation size. For large allocations (>16GB), larger chunks are recommended for faster startup. For older or memory-constrained systems, smaller chunks may be more reliable.

Syntax:
```
-chunk=SIZE
```
Where SIZE can be specified as:
- A number in MB: `-chunk=100`
- With units: `-chunk=1GB`, `-chunk=512MB`

Default: 100MB

### Intensity Level (-intensity)

Controls how aggressively JAWS accesses memory, directly affecting CPU usage and memory bus activity:

- **Level 1-3 (Light)**: Minimal memory access, just enough to prevent swapping
  - Low CPU usage (5-15%)
  - Ideal for long-running tests where CPU usage should be minimized

- **Level 4-6 (Moderate)**: Balanced memory access
  - Moderate CPU usage (15-40%)
  - Good for most testing scenarios

- **Level 7-8 (Heavy)**: Aggressive memory access patterns
  - Higher CPU usage (40-70%)
  - Multiple thread types with varied access patterns
  - Good for simulating memory-intensive applications

- **Level 9-10 (Extreme)**: Maximum memory stress
  - Very high CPU usage (70-100%)
  - Aggressive sequential and random access patterns
  - Cache-unfriendly access to maximize memory bus utilization
  - Suitable for stress testing and bandwidth simulation

Default: 5 (Moderate)

### Static Mode (-static)

By default, JAWS runs in dynamic mode with active memory access patterns. Static mode:

- Creates and locks memory but minimizes active access
- Still performs some background access to prevent swapping
- Uses less CPU while still maintaining locked memory
- Useful for scenarios where you want memory consumption without CPU load

## Memory Locking Details

JAWS uses multiple approaches to ensure allocated memory remains in physical RAM:

1. **mlockall() System Call**: Instructs the kernel to lock all current and future memory allocations
2. **Memory Touching**: Writes to every page to ensure it's mapped into physical memory
3. **Active Access Patterns**: Continuously accesses memory to prevent the kernel from considering it inactive
4. **Process Priority**: Sets high process priority to reduce likelihood of swapping
5. **OOM Score Adjustment**: Makes the process less likely to be killed under memory pressure
6. **Swappiness Configuration**: Reduces system-wide tendency to swap memory

## System Restoration

After running JAWS, use the automatically generated restore script to reset your system:

```bash
sudo /usr/local/bin/restore_jaws_settings
```

Alternatively, use the standalone deconstruction script:

```bash
sudo ./deconstruct_jaws.sh
```

Both methods restore:
- Original swappiness settings
- Original memory lock limits
- Other system parameters

## Monitoring

While JAWS is running, you can monitor its impact using standard Linux tools:

```bash
# Monitor memory usage
free -m

# Check if memory is being swapped
cat /proc/$(pgrep -f jaws.py)/status | grep VmSwap

# Monitor overall system performance
vmstat 5
```

JAWS also reports its own memory utilization and CPU usage periodically during execution.

## Troubleshooting

### Memory Allocation Failures

If JAWS fails to allocate memory:

1. Try reducing the percentage (-low instead of -mid or -high, or a lower custom percentage)
2. Use smaller chunk sizes (-chunk=50MB)
3. Check available memory with `free -m`

### Memory Locking Failures

If memory locking warnings appear:

1. Ensure you're running with sudo/root privileges
2. Check current limits: `ulimit -a | grep "max locked memory"`
3. Some systems may require a reboot after limit changes

### System Unresponsiveness

If the system becomes unresponsive with high intensity levels:

1. Reduce intensity level (-intensity=3)
2. Use the -static option to minimize CPU usage
3. Reduce the percentage of memory being allocated

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

JAWS was developed to assist with hardware testing and memory subsystem analysis.

## Maintainer

Maintained by [Kyle Fox](https://github.com/kylefoxaustin)

---

## Maintainer Notes

> These notes are for maintainers only and should not be considered part of the public documentation.

### Development Roadmap

- Consider adding variable memory access patterns (sinusoidal, burst, etc.)
- Implement CPU affinity controls to target specific cores/NUMA nodes
- Add detailed memory timing and latency measurements
- Create a GUI front-end for easier configuration

### Known Issues

- Very large allocations (>128GB) may require additional optimizations
- Systems with large NUMA configurations need special handling
- Compatibility with non-Linux platforms is limited

### Testing Priorities

1. Verify memory remains locked across various system loads
2. Ensure system restoration works properly after abnormal termination
3. Test on different hardware configurations (server, desktop, embedded)
4. Monitor for memory fragmentation issues on long-running tests

### Code Structure

The codebase is organized into these main components:
- Memory allocation and locking (create_buffer)
- Access pattern generation (random_access and related methods)
- System configuration (setup_for_jaws.sh)
- Cleanup and restoration (deconstruct_jaws.sh)

Any modifications should maintain separation between these concerns.
