#!/bin/bash
# setup_for_jaws.sh - Configure system for optimal memory locking with backup

# Requires root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

# Create backup directory
BACKUP_DIR="/tmp/jaws_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
echo "Created backup directory: $BACKUP_DIR"

# Backup current swappiness setting
CURRENT_SWAPPINESS=$(cat /proc/sys/vm/swappiness)
echo "$CURRENT_SWAPPINESS" > "$BACKUP_DIR/swappiness.bak"
echo "Backed up current swappiness value: $CURRENT_SWAPPINESS"

# Backup current min_free_kbytes setting
CURRENT_MIN_FREE=$(cat /proc/sys/vm/min_free_kbytes)
echo "$CURRENT_MIN_FREE" > "$BACKUP_DIR/min_free_kbytes.bak"
echo "Backed up current min_free_kbytes value: $CURRENT_MIN_FREE"

# Backup limits.conf if it exists
if [ -f "/etc/security/limits.conf" ]; then
  cp /etc/security/limits.conf "$BACKUP_DIR/limits.conf.bak"
  echo "Backed up /etc/security/limits.conf"
fi

# Create a restore script in the backup directory
cat > "$BACKUP_DIR/restore_settings.sh" << 'EOF'
#!/bin/bash
# restore_settings.sh - Restore original system settings

# Requires root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

# Directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Restore swappiness if backup exists
if [ -f "$SCRIPT_DIR/swappiness.bak" ]; then
  ORIGINAL_SWAPPINESS=$(cat "$SCRIPT_DIR/swappiness.bak")
  echo "Restoring swappiness to $ORIGINAL_SWAPPINESS..."
  echo $ORIGINAL_SWAPPINESS > /proc/sys/vm/swappiness
fi

# Restore min_free_kbytes if backup exists
if [ -f "$SCRIPT_DIR/min_free_kbytes.bak" ]; then
  ORIGINAL_MIN_FREE=$(cat "$SCRIPT_DIR/min_free_kbytes.bak")
  echo "Restoring min_free_kbytes to $ORIGINAL_MIN_FREE..."
  echo $ORIGINAL_MIN_FREE > /proc/sys/vm/min_free_kbytes
fi

# Restore limits.conf if backup exists
if [ -f "$SCRIPT_DIR/limits.conf.bak" ]; then
  echo "Restoring original limits.conf..."
  cp "$SCRIPT_DIR/limits.conf.bak" /etc/security/limits.conf

  # Apply limits without reboot using pam_limits
  if [ -d "/etc/pam.d" ]; then
    # Ensure pam_limits is included in common-session
    if ! grep -q "pam_limits.so" /etc/pam.d/common-session 2>/dev/null; then
      echo "Warning: pam_limits.so not found in PAM configuration."
      echo "Changes to limits.conf may require a reboot to fully take effect."
    else
      echo "PAM limits module is configured. New limits should apply to new sessions."
    fi
  fi
fi

echo "System settings restored to original values."
EOF

# Make the restore script executable
chmod +x "$BACKUP_DIR/restore_settings.sh"
echo "Created restore script: $BACKUP_DIR/restore_settings.sh"

# Create a symbolic link to the restore script in a more accessible location
RESTORE_LINK="/usr/local/bin/restore_jaws_settings"
ln -sf "$BACKUP_DIR/restore_settings.sh" "$RESTORE_LINK"
echo "Created restore script link: $RESTORE_LINK"

# Reduce swappiness (how aggressively the kernel swaps)
echo "Setting vm.swappiness to 10..."
echo 10 > /proc/sys/vm/swappiness

# Increase min_free_kbytes to ensure some memory is always available
mem_total=$(grep MemTotal /proc/meminfo | awk '{print $2}')
min_free=$((mem_total / 50))  # 2% of total memory
echo "Setting vm.min_free_kbytes to $min_free..."
echo $min_free > /proc/sys/vm/min_free_kbytes

# Display current limits
echo "Current memory lock limits:"
ulimit -a | grep "max locked memory"

# Temporarily increase limits for locked memory for this session
echo "Setting unlimited locked memory for this session..."
ulimit -l unlimited

# Update limits.conf with new settings, preserving comments and other entries
if [ -f "/etc/security/limits.conf" ]; then
  # Create a temporary file
  TEMP_LIMITS=$(mktemp)

  # Remove any existing memlock entries for * and root
  grep -v -E "^\*.*memlock|^root.*memlock" /etc/security/limits.conf > "$TEMP_LIMITS"

  # Add new memlock entries at the end
  echo "# Added by jaws setup script" >> "$TEMP_LIMITS"
  echo "* soft memlock unlimited" >> "$TEMP_LIMITS"
  echo "* hard memlock unlimited" >> "$TEMP_LIMITS"
  echo "root soft memlock unlimited" >> "$TEMP_LIMITS"
  echo "root hard memlock unlimited" >> "$TEMP_LIMITS"

  # Replace the original file
  cp "$TEMP_LIMITS" /etc/security/limits.conf
  rm "$TEMP_LIMITS"

  echo "Updated /etc/security/limits.conf with memlock settings"

  # Try to apply limits without requiring reboot
  if [ -d "/etc/pam.d" ]; then
    # Check if pam_limits is already enabled in common-session
    if grep -q "pam_limits.so" /etc/pam.d/common-session 2>/dev/null; then
      echo "PAM limits module is already configured."
      echo "New limits will apply to new login sessions."
    else
      echo "Warning: Unable to confirm pam_limits.so is enabled."
      echo "New limits may require a login or reboot to take effect."
    fi
  fi
else
  echo "Warning: /etc/security/limits.conf not found. Cannot update memory lock limits."
fi

echo "System configured for optimal memory locking."
echo "To restore original settings, run: sudo $RESTORE_LINK"

# Process script arguments to handle the -percent option
JAWS_ARGS=()
CUSTOM_PERCENT=false
PERCENT_VALUE=0

for arg in "$@"; do
  # Check if this is a percent argument
  if [[ "$arg" =~ ^-percent$ ]]; then
    CUSTOM_PERCENT=true
  elif [[ "$CUSTOM_PERCENT" == true && "$arg" =~ ^[0-9]+$ ]]; then
    PERCENT_VALUE="$arg"
    CUSTOM_PERCENT=false
    JAWS_ARGS+=("-percent" "$arg")
  else
    JAWS_ARGS+=("$arg")
  fi
done

# Run jaws with the arguments
if [ ${#JAWS_ARGS[@]} -gt 0 ]; then
    echo "Running jaws with arguments: ${JAWS_ARGS[@]}"
    exec python3 ./jaws.py "${JAWS_ARGS[@]}"
else
    echo "Setup complete. You can now run jaws.py with your preferred arguments."
fi
