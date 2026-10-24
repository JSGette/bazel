#!/usr/bin/env bash
set -euo pipefail
image=/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work/skycache-v2-data.ext4
target=/mnt/skycache-v2-local
test ! -e "$image"
test ! -e "$target"
truncate -s 64G "$image"
mkfs.ext4 -q -m 0 -L skycache-v2 "$image"
mkdir "$target"
mount -o loop,noatime "$image" "$target"
chown 1000:1000 "$target"
findmnt --target "$target"
df -h "$target"
