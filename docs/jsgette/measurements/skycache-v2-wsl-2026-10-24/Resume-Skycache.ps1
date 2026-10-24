param([switch]$OpenShell)
$ErrorActionPreference = 'Stop'

# Mount the existing task-owned ext4 image after a WSL restart.
# This does not format, delete, or rebuild anything.
$taskMountScript = @'
set -euo pipefail
image=/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work/skycache-v2-data.ext4
target=/mnt/skycache-v2-local
test -f "$image"
if mountpoint -q "$target"; then
    device=$(findmnt -n -o SOURCE --target "$target")
    actual=$(losetup -n -O BACK-FILE "$device")
    test "$actual" = "$image"
else
    mkdir -p "$target"
    mount -o loop,noatime "$image" "$target"
fi
findmnt --target "$target"
'@
$taskMountScript | wsl.exe -d Ubuntu -u root -- bash -c "tr -d '\r' | bash"
if ($LASTEXITCODE -ne 0) { throw 'SkyCache volume verification or mount failed.' }
Write-Output 'Source: /home/jgett/REPOS/bazel-skycache-v2 (jsgette/skycache-v2-local)'
Write-Output 'Results: /mnt/skycache-v2-local'
Write-Output 'Documentation: /home/jgett/REPOS/bazel-skyframe-findings (jsgette/master)'
if ($OpenShell) {
    wsl.exe -d Ubuntu --cd /home/jgett/REPOS/bazel-skycache-v2 --exec bash -l
}
