# CamShaft Inspection Station - in-place updater.
# Replaces only the program files; keeps config, reports, logs, frames, result_images,
# retrained models and your own data. The previous version is kept in _previous_version for rollback.
#
#   Update_Station.bat                       -> download the latest GitHub release
#   Update_Station.bat C:\path\release.zip   -> offline: use a downloaded release zip
param([string]$Zip = "")
$ErrorActionPreference = "Stop"
$App = Split-Path -Parent $MyInvocation.MyCommand.Path
$Exe = Join-Path $App "CamShaft_Inspection_Station.exe"
$Repo = "Yadav-Roshan/thread-detection-2d-matrix-reader"
$Keep = @("config", "reports", "logs", "frames", "result_images", "models", "_previous_version")

function Say($m) { Write-Host "==> $m" }

if (-not (Test-Path $Exe)) { throw "Run this from the station folder (CamShaft_Inspection_Station.exe not found in $App)." }

# 1. Get the release zip
if (-not $Zip) {
    $local = Get-ChildItem -Path $App -Filter "CamShaft_Inspection_Station-win64*.zip" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($local) { $Zip = $local.FullName }
}
$Tmp = Join-Path $env:TEMP ("csu" + [guid]::NewGuid().ToString("N").Substring(0, 6))   # short: Windows 260-char path limit
New-Item -ItemType Directory -Force $Tmp | Out-Null
if (-not $Zip) {
    Say "Checking the latest release on GitHub"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "CamShaft-Updater" }
    $asset = $rel.assets | Where-Object { $_.name -like "*win64*.zip" } | Select-Object -First 1
    if (-not $asset) { throw "No Windows zip found in release $($rel.tag_name)." }
    $Zip = Join-Path $Tmp $asset.name
    Say "Downloading $($rel.tag_name) ($([math]::Round($asset.size / 1MB)) MB)"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $Zip -UseBasicParsing
}
Say "Using $Zip"
Expand-Archive -Path $Zip -DestinationPath $Tmp -Force
$New = Get-ChildItem -Path $Tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "CamShaft_Inspection_Station.exe") } | Select-Object -First 1
if (-not $New) { throw "The zip does not contain CamShaft_Inspection_Station.exe." }

# 2. Stop the running station (only the copy in this folder)
$running = Get-Process -Name "CamShaft_Inspection_Station" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Exe }
if ($running) { Say "Stopping the running station"; $running | Stop-Process -Force; Start-Sleep -Seconds 2 }

# 3. Keep the current version for rollback
$Prev = Join-Path $App "_previous_version"
if (Test-Path $Prev) { Remove-Item -Recurse -Force $Prev }
New-Item -ItemType Directory -Force $Prev | Out-Null
foreach ($item in @("CamShaft_Inspection_Station.exe", "_internal", "README.txt")) {
    $src = Join-Path $App $item
    if (Test-Path $src) { Move-Item -Force $src (Join-Path $Prev $item) }
}

# 4. Copy the new program files (never the folders that hold your settings and results)
Say "Installing the new version"
Get-ChildItem -Path $New.FullName | Where-Object { $Keep -notcontains $_.Name -and $_.Name -ne "data" } | ForEach-Object {
    Copy-Item -Recurse -Force $_.FullName (Join-Path $App $_.Name)
}
# Training images: add new ones, never delete or overwrite yours
$newData = Join-Path $New.FullName "data"
if (Test-Path $newData) {
    Get-ChildItem -Path $newData -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($newData.Length).TrimStart('\')
        $dst = Join-Path (Join-Path $App "data") $rel
        if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null; Copy-Item $_.FullName $dst }
    }
}
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue

Say "Update complete. Settings, reports and results were kept. Previous version: $Prev"
Say "Starting the station"
Start-Process -FilePath $Exe -WorkingDirectory $App
