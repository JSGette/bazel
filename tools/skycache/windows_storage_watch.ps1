# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
# Start on Windows; pass its drvfs path through SKYCACHE_WINDOWS_STORAGE_SNAPSHOT in WSL.
# The reader rejects observations older than 30 seconds. The watcher never deletes user data.
param(
    [Parameter(Mandatory=$true)][string]$SnapshotPath,
    [Parameter(Mandatory=$true)][string]$StopFile,
    [ValidateRange(1, 43200)][int]$MaximumSeconds = 43200
)
$ErrorActionPreference = 'Stop'
$snapshot = [IO.Path]::GetFullPath($SnapshotPath)
$stop = [IO.Path]::GetFullPath($StopFile)
if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($snapshot))) {
    throw 'Snapshot parent directory must already exist'
}
if ($snapshot -eq $stop) { throw 'Snapshot and stop paths must differ' }
$temporary = $snapshot + '.pending.' + $PID
$deadline = [DateTime]::UtcNow.AddSeconds($MaximumSeconds)
$encoding = New-Object System.Text.UTF8Encoding($false)
function Read-Storage {

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
    schema_version=1
    observed_unix=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
} | ConvertTo-Json -Depth 4 -Compress

}
try {
    while ([DateTime]::UtcNow -lt $deadline -and -not [IO.File]::Exists($stop)) {
        $json = Read-Storage
        [IO.File]::WriteAllText($temporary, $json, $encoding)
        if ([IO.File]::Exists($snapshot)) {
            [IO.File]::Replace($temporary, $snapshot, [System.Management.Automation.Language.NullString]::Value)
        } else {
            [IO.File]::Move($temporary, $snapshot)
        }
        Start-Sleep -Seconds 5
    }
} finally {
    if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
}
