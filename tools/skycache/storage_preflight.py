#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Check probe storage, including the host volume behind a growing WSL virtual disk."""

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

GIB = 1024**3

# Keep this script constant: paths and distro names are data returned to Python, not shell code.
_WINDOWS_STORAGE = r"""
$ErrorActionPreference = 'Stop'
$systemDrive = [IO.DriveInfo]::new($env:SystemDrive + '\')
$distros = @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
    Get-ItemProperty | ForEach-Object {
        $distro = $_
        try {
            $base = $distro.BasePath
            if ($base.StartsWith('\\?\')) { $base = $base.Substring(4) }
            $drive = [IO.DriveInfo]::new((Split-Path -Qualifier $base) + '\')
            [pscustomobject]@{
                name=$distro.DistributionName
                path=$distro.BasePath
                free_bytes=$drive.AvailableFreeSpace
            }
        } catch {
            [pscustomobject]@{ name=$distro.DistributionName; error=$_.Exception.Message }
        }
    })
[pscustomobject]@{
    system_drive=$systemDrive.Name
    system_free_bytes=$systemDrive.AvailableFreeSpace
    distros=$distros
} | ConvertTo-Json -Depth 4 -Compress
"""


def _wsl_storage():
    name = os.environ.get("WSL_DISTRO_NAME")
    if not name and "microsoft" not in platform.release().lower():
        return None
    if not name:
        raise RuntimeError("WSL detected but WSL_DISTRO_NAME is missing; cannot check its backing volume")
    for attempt in range(3):
        try:
            data = json.loads(subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_STORAGE],
                text=True, stderr=subprocess.PIPE, timeout=20))
            break
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            if attempt == 2:
                detail = getattr(error, "stderr", None) or str(error)
                if isinstance(detail, bytes):
                    detail = detail.decode(errors="replace")
                raise RuntimeError(
                    "Cannot check WSL host storage after 3 attempts: " + detail.strip()[-2000:]
                ) from error
            # Interop can fail transiently while the VM is busy. Never reuse an old capacity
            # observation or disable the guard; every retry asks Windows for current values.
            time.sleep(0.5 * (attempt + 1))
    matches = [d for d in data["distros"] if d["name"] == name]
    if len(matches) != 1 or "error" in matches[0]:
        raise RuntimeError(f"Cannot resolve the Windows backing volume for WSL distro {name!r}")
    return {"distro": name, "distro_path": matches[0]["path"],
            "backing_free_bytes": int(matches[0]["free_bytes"]),
            "system_drive": data["system_drive"],
            "system_free_bytes": int(data["system_free_bytes"])}


def check_storage(paths, *, minimum_bytes=30 * GIB, system_reserve_bytes=10 * GIB):
    """Fail before starting a measured command; return evidence without creating any files."""
    volumes = []
    seen = set()
    for requested in paths:
        existing = Path(requested).resolve()
        while not existing.exists():
            existing = existing.parent
        device = existing.stat().st_dev
        if device in seen:
            continue
        seen.add(device)
        free = shutil.disk_usage(existing).free
        if free < minimum_bytes:
            raise RuntimeError(
                f"Insufficient storage at {existing}: {free / GIB:.1f} GiB free; "
                f"probe requires {minimum_bytes / GIB:.1f} GiB")
        volumes.append({"path": str(existing), "free_bytes": free})
    host = _wsl_storage()
    if host is not None:
        if host["backing_free_bytes"] < minimum_bytes:
            raise RuntimeError(
                f"WSL backing volume at {host['distro_path']} has "
                f"{host['backing_free_bytes'] / GIB:.1f} GiB free; "
                f"probe requires {minimum_bytes / GIB:.1f} GiB")
        if host["system_free_bytes"] < system_reserve_bytes:
            raise RuntimeError(
                f"Windows system drive {host['system_drive']} has "
                f"{host['system_free_bytes'] / GIB:.1f} GiB free; "
                f"probe reserves {system_reserve_bytes / GIB:.1f} GiB")
    return {"minimum_free_bytes": minimum_bytes, "system_reserve_bytes": system_reserve_bytes,
            "filesystems": volumes, "wsl_host": host}
