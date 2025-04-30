#!/bin/bash
# deconstruct_jaws.sh - Standalone script to restore system settings after using jaws
# This script can be used even if you've lost the automatically generated restore script

# Requires root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

echo "Jaws Deconstruction: Restoring system to original state..."

# Try to find the auto-generated restore script first
RESTORE_LINK="/usr/local/bin/restore_jaws_settings"
if [ -L "$RESTORE_LINK" ] && [ -x "$(readlink -f "$RESTORE_LINK")" ]; then
  echo "Found auto-generated restore script. Running it..."
  "$RESTORE_LINK"
  echo "Removing restore script link..."
  rm "$RESTORE_LINK"
  exit 0
fi

echo "No auto-generated restore script found. Performing manual restoration..."

# Restore default swappiness (60 is the common default)
DEFAULT_SWAPPINESS=60
echo "Restoring vm.swappiness to default value ($DEFAULT_SWAPPINESS)..."
echo $DEFAULT_SWAPPINESS > /proc/sys/vm/swappiness

# Restore default min_free_kbytes
# This varies by system, but we'll use a reasonable default based on memory size
mem_total=$(grep MemTotal /proc/meminfo | awk '{print $2}')
if [ $mem_total -lt 1048576 ]; then # Less than 1GB
  DEFAULT_MIN_FREE=8192
elif [ $mem_total -lt 4194304 ]; then # 1-4GB
  DEFAULT_MIN_FREE=16384
elif [ $mem_total -lt 16777216 ]; then # 4-16GB
  DEFAULT_MIN_FREE=32768
else # More than 16GB
  DEFAULT_MIN_FREE=65536
fi

echo "Restoring vm.min_free_kbytes to default value ($DEFAULT_MIN_FREE)..."
echo $DEFAULT_MIN_FREE > /proc/sys/vm/min_free_kbytes

# Restore limits.conf if it exists
if [ -f "/etc/security/limits.conf" ]; then
  echo "Cleaning up memlock entries from /etc/security/limits.conf..."

  # Create a temporary file
  TEMP_LIMITS=$(mktemp)

  # Remove jaws-added memlock entries
  grep -v -E "^\*.*memlock|^root.*memlock|^# Added by jaws setup script" /etc/security/limits.conf > "$TEMP_LIMITS"

  # Replace the original file
  cp "$TEMP_LIMITS" /etc/security/limits.conf
  rm "$TEMP_LIMITS"

  echo "Cleaned up /etc/security/limits.conf"
fi

# Look for backup directories in /tmp
BACKUP_DIRS=$(find /tmp -maxdepth 1 -name "jaws_backup_*" -type d 2>/dev/null)
if [ -n "$BACKUP_DIRS" ]; then
  echo "Found the following jaws backup directories that can be removed:"
  echo "$BACKUP_DIRS"

  read -p "Do you want to remove these backup directories? (y/n): " REMOVE_BACKUP
  if [[ "$REMOVE_BACKUP" =~ ^[Yy]$ ]]; then
    echo "Removing backup directories..."
    rm -rf $(echo "$BACKUP_DIRS")
    echo "Backup directories removed."
  else
    echo "Backup directories preserved."
  fi
fi

echo "System settings restored to default values."
echo "Note: Some changes may require a reboot to fully take effect."
