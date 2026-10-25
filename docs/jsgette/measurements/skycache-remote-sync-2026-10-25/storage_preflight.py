#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Check probe storage, including the host volume behind a growing WSL virtual disk."""

import json
import math
import os
from pathlib import Path
import platform
import re
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
    drives=@([IO.DriveInfo]::GetDrives() | Where-Object {
        $_.DriveType -eq [IO.DriveType]::Fixed -and $_.IsReady
    } | ForEach-Object {
        [pscustomobject]@{root=$_.Name; free_bytes=$_.AvailableFreeSpace}
    })
} | ConvertTo-Json -Depth 4 -Compress
"""


def _wsl_storage():
    name = os.environ.get("WSL_DISTRO_NAME")
    if not name and "microsoft" not in platform.release().lower():
        return None
    if not name:
        raise RuntimeError("WSL detected but WSL_DISTRO_NAME is missing; cannot check its backing volume")
    snapshot = os.environ.get("SKYCACHE_WINDOWS_STORAGE_SNAPSHOT")
    if snapshot:
        # A Windows-side watcher avoids repeated WSL interop launches under build load.
        # Stopped, delayed or malformed watchers must still fail the same capacity guard.
        try:
            path = Path(snapshot)
            for attempt in range(3):
                try:
                    # Open once: a separate stat/read can straddle a Windows replacement.
                    # drvfs can briefly report ENOENT while the host replaces the file.
                    with path.open("rb") as stream:
                        encoded = stream.read(64 * 1024 + 1)
                    if len(encoded) > 64 * 1024:
                        raise ValueError("snapshot exceeds 64 KiB")
                    data = json.loads(encoded.decode("utf-8"))
                    break
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    if attempt == 2:
                        raise ValueError("snapshot read failed after 3 attempts: " + str(error)) from error
                    # Every retry reads the current file. No cached capacity is reused;
                    # persistent failures still cancel the probe after at most 0.3s of delay.
                    time.sleep(0.1 * (attempt + 1))
            observed = data["observed_unix"]
            if isinstance(observed, bool) or not isinstance(observed, (int, float)):
                raise ValueError("invalid observation timestamp")
            age = time.time() - observed
            # Windows and WSL clocks can differ by a fraction of a second.
            if not math.isfinite(age) or not -1 <= age <= 30:
                raise ValueError(f"observation age {age} is outside -1..30 seconds")
            if type(data["schema_version"]) is not int or data["schema_version"] != 1:
                raise ValueError("unsupported observation schema")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RuntimeError("Cannot verify fresh Windows storage snapshot: " + str(error)) from error
    else:
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
            "system_free_bytes": int(data["system_free_bytes"]),
            "windows_drives": {d["root"].upper(): int(d["free_bytes"]) for d in data.get("drives", [])},
            "observation_source": str(snapshot) if snapshot else "wsl_interop",
            "observed_unix": data.get("observed_unix")}


def _loop_windows_backing(path):
    """Resolve a Linux loop filesystem through kernel metadata to its actual Windows drive.

    Ordinary WSL ext4 and Linux-backed loop files still use the distro's backing-volume guard.
    Only a verified drvfs backing file can replace that guard for the corresponding write path.
    """
    device = Path(path).stat().st_dev
    if os.major(device) != 7:
        return None
    sysfs = Path(f"/sys/dev/block/{os.major(device)}:{os.minor(device)}/loop/backing_file")
    try:
        backing = Path(sysfs.read_text().strip()).resolve(strict=True)
        if not backing.is_file():
            raise RuntimeError("Loop backing path is not a regular file")
        mount = json.loads(subprocess.check_output([
            "findmnt", "--json", "--target", str(backing), "--output", "SOURCE,FSTYPE,TARGET"],
            text=True, stderr=subprocess.PIPE, timeout=10))["filesystems"][0]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as error:
        raise RuntimeError("Cannot verify loop filesystem backing storage: " + str(path)) from error
    if mount["fstype"] not in ("9p", "drvfs"):
        if mount["source"].startswith("/dev/loop"):
            raise RuntimeError("Nested loop backing storage is unsupported")
        return None
    drive = mount["source"].upper()
    if re.fullmatch(r"[A-Z]:\\", drive) is None:
        raise RuntimeError("Cannot identify Windows drive for loop backing file: " + str(backing))
    return {"drive": drive, "image": str(backing), "kernel_backing_file": str(sysfs),
            "backing_mount": mount}


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
        for volume in volumes:
            backing = _loop_windows_backing(volume["path"])
            if backing is None:
                if host["backing_free_bytes"] < minimum_bytes:
                    raise RuntimeError(
                        f"WSL backing volume at {host['distro_path']} has "
                        f"{host['backing_free_bytes'] / GIB:.1f} GiB free; "
                        f"probe requires {minimum_bytes / GIB:.1f} GiB")
                volume["host_backing"] = {"kind": "wsl_distro", "path": host["distro_path"],
                                          "free_bytes": host["backing_free_bytes"]}
            else:
                free = host.get("windows_drives", {}).get(backing["drive"])
                if free is None:
                    raise RuntimeError("No current Windows capacity observation for " + backing["drive"])
                if free < minimum_bytes:
                    raise RuntimeError(
                        f"Loop backing volume {backing['drive']} has {free / GIB:.1f} GiB free; "
                        f"probe requires {minimum_bytes / GIB:.1f} GiB")
                volume["host_backing"] = {"kind": "windows_loop_image", **backing, "free_bytes": free}
        if host["system_free_bytes"] < system_reserve_bytes:
            raise RuntimeError(
                f"Windows system drive {host['system_drive']} has "
                f"{host['system_free_bytes'] / GIB:.1f} GiB free; "
                f"probe reserves {system_reserve_bytes / GIB:.1f} GiB")
    return {"minimum_free_bytes": minimum_bytes, "system_reserve_bytes": system_reserve_bytes,
            "filesystems": volumes, "wsl_host": host}
