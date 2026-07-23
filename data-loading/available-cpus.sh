#!/usr/bin/env bash
#
# Print the number of CPUs this process is actually allowed to use.
#
# getconf and nproc report the *node's* CPU count even inside a container with a
# CPU limit: in a pod limited to 8 CPUs on a 64-core node, both say 64. Sizing a
# thread pool from that number means 64 workers fighting over 8 CPUs' worth of
# quota, which is slower than 8 workers, not faster. So read the cgroup quota and
# only fall back to the node count when there isn't one.

set -uo pipefail

fallback=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)

if [ -r /sys/fs/cgroup/cpu.max ]; then
  # cgroup v2: "<quota> <period>", where quota is the string "max" if unlimited.
  read -r quota period < /sys/fs/cgroup/cpu.max
elif [ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]; then
  # cgroup v1: separate files, quota is -1 if unlimited.
  quota=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us)
  period=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)
fi

# Left to right, and [ stops at the first true test: "max" is never compared
# numerically, which would be an error.
if [ "${quota:-max}" = "max" ] || [ "${quota:-0}" -le 0 ] || [ "${period:-0}" -le 0 ]; then
  echo "$fallback"
  exit 0
fi

# Round down (a 1.5-CPU quota gets one worker), but never below one.
cpus=$((quota / period))
[ "$cpus" -lt 1 ] && cpus=1
echo "$cpus"
