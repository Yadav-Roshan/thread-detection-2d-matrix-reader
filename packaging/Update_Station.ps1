# CamShaft Inspection Station - in-place updater (v2).
# Replaces only the program files; keeps config, reports, logs, frames, result_images,
# retrained models and your own data. The previous version is kept in _previous_version,
# and is restored automatically if the install step fails.
#
# Embedded into the single-file Update_Station.bat by scripts/make_updater.py (loaded as text,
# so PC script-execution policies do not block it).
#   Update_Station.bat                     -> latest GitHub release (or a release zip found in this folder)
#   Update_Station.bat C:\path\release.zip -> use that zip (offline PCs)

$ErrorActionPreference = "Stop"
$App = $env:CAMSHAFT_APP_DIR
if (-not $App) { $App = Split-Path -Parent $MyInvocation.MyCommand.Path }
$App = $App.TrimEnd('\')
$Zip = $env:CAMSHAFT_UPDATE_ZIP
$Exe = Join-Path $App "CamShaft_Inspection_Station.exe"
$Repo = "Yadav-Roshan/thread-detection-2d-matrix-reader"
$Keep = @("config", "reports", "logs", "frames", "result_images", "models", "_previous_version", "Update_Station.log")
$Log = Join-Path $App "Update_Station.log"

function Say([string]$m) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"
    Write-Host "==> $m"
    Add-Content -Path $Log -Value $line -ErrorAction SilentlyContinue
}
function Fail([string]$m) {
    Say "FAILED: $m"
    Write-Host ""
    Write-Host "The update did not complete. Details are in: $Log" -ForegroundColor Red
    exit 1
}

Say "CamShaft station updater started in $App"
if (-not (Test-Path $Exe)) {
    Fail "CamShaft_Inspection_Station.exe was not found here. Put Update_Station.bat in the station folder (next to CamShaft_Inspection_Station.exe) and double-click it there."
}

# Remove the 'downloaded from the internet' mark from the updater files (harmless if absent)
Get-ChildItem -Path $App -Filter "Update_Station.*" | Unblock-File -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- 1. get the release zip
if (-not $Zip) {
    $local = Get-ChildItem -Path $App -Filter "CamShaft_Inspection_Station-win64*.zip" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($local) { $Zip = $local.FullName; Say "Found release zip in the station folder: $($local.Name)" }
}
$Tmp = Join-Path $env:TEMP ("csu" + [guid]::NewGuid().ToString("N").Substring(0, 6))   # short path (Windows 260-char limit)
New-Item -ItemType Directory -Force $Tmp | Out-Null

if (-not $Zip) {
    Say "Checking the latest release on GitHub"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $proxy = [System.Net.WebRequest]::GetSystemWebProxy()
        $proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials   # company proxy with Windows login
        [System.Net.WebRequest]::DefaultWebProxy = $proxy
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "CamShaft-Updater" } -UseBasicParsing
    } catch {
        Fail ("This PC could not reach GitHub ($($_.Exception.Message)). Offline update: on a PC with internet, " +
              "download CamShaft_Inspection_Station-win64.zip from https://github.com/$Repo/releases/latest, copy it " +
              "into $App and run Update_Station.bat again.")
    }
    $asset = $rel.assets | Where-Object { $_.name -like "*win64*.zip" } | Select-Object -First 1
    if (-not $asset) { Fail "No Windows zip found in release $($rel.tag_name)." }
    $Zip = Join-Path $Tmp $asset.name
    Say "Downloading $($rel.tag_name) ($([math]::Round($asset.size / 1MB)) MB) - this can take a few minutes"
    try {
        $ProgressPreference = "SilentlyContinue"   # the progress bar makes downloads 10x slower in Windows PowerShell
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $Zip -UseBasicParsing
    } catch { Fail "Download failed: $($_.Exception.Message)" }
}
if (-not (Test-Path $Zip)) { Fail "Release zip not found: $Zip" }
Say "Using $Zip"

Say "Unpacking (about 1 minute)"
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, (Join-Path $Tmp "x"))
} catch { Fail "Could not unpack the zip: $($_.Exception.Message)" }
$New = Get-ChildItem -Path (Join-Path $Tmp "x") -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName "CamShaft_Inspection_Station.exe") } | Select-Object -First 1
if (-not $New) { Fail "The zip does not contain CamShaft_Inspection_Station.exe." }

# ---------------------------------------------------------------- 2. stop the running station (this folder only)
$running = Get-Process -Name "CamShaft_Inspection_Station" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Exe }
if ($running) {
    Say "Stopping the running station"
    $running | Stop-Process -Force
    $running | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
if (Get-Process -Name "CamShaft_Inspection_Station" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Exe }) {
    Fail "The station is still running. Close its black console window and run the update again."
}

# ---------------------------------------------------------------- 3. install with automatic rollback
$Prev = Join-Path $App "_previous_version"
$programItems = @("CamShaft_Inspection_Station.exe", "_internal", "README.txt")
try {
    if (Test-Path $Prev) { Remove-Item -Recurse -Force $Prev }
    New-Item -ItemType Directory -Force $Prev | Out-Null
    foreach ($item in $programItems) {
        $src = Join-Path $App $item
        if (Test-Path $src) { Move-Item -Force $src (Join-Path $Prev $item) }
    }
    Say "Installing the new version"
    Get-ChildItem -Path $New.FullName | Where-Object { $Keep -notcontains $_.Name -and $_.Name -ne "data" -and $_.Name -notlike "Update_Station.*" } |
        ForEach-Object { Copy-Item -Recurse -Force $_.FullName (Join-Path $App $_.Name) }
    # Training images: add new ones, never delete or overwrite yours
    $newData = Join-Path $New.FullName "data"
    if (Test-Path $newData) {
        Get-ChildItem -Path $newData -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($newData.Length).TrimStart('\')
            $dst = Join-Path (Join-Path $App "data") $rel
            if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null; Copy-Item $_.FullName $dst }
        }
    }
    if (-not (Test-Path $Exe)) { throw "new program files were not copied" }
} catch {
    $err = $_.Exception.Message
    Say "Install failed ($err) - restoring the previous version"
    foreach ($item in $programItems) {
        $dst = Join-Path $App $item; $bak = Join-Path $Prev $item
        if (Test-Path $bak) {
            if (Test-Path $dst) { Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue }
            Move-Item -Force $bak $dst
        }
    }
    Fail "Install failed and the previous version was restored: $err"
}

# Update the updater itself. cmd.exe is still reading Update_Station.bat, so the new copy is put in
# place by a separate hidden process a few seconds after this window has finished.
$newBat = Join-Path $New.FullName "Update_Station.bat"
$staged = $null
if (Test-Path $newBat) {
    $staged = Join-Path $env:TEMP ("csu_bat_" + [guid]::NewGuid().ToString("N").Substring(0, 6) + ".bat")
    Copy-Item -Force $newBat $staged
    $target = Join-Path $App "Update_Station.bat"
    $cmd = "/c ping -n 8 127.0.0.1 >nul & copy /y `"$staged`" `"$target`" >nul & del `"$staged`""
    Start-Process -FilePath "cmd.exe" -ArgumentList $cmd -WindowStyle Hidden
}
$oldPs1 = Join-Path $App "Update_Station.ps1"   # no longer needed: the .bat is self-contained
if (Test-Path $oldPs1) { Remove-Item -Force $oldPs1 -ErrorAction SilentlyContinue }
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue

$ver = (Get-Item $Exe).VersionInfo.ProductVersion
Say "Update complete: version $ver. Settings, reports and results were kept. Previous version: $Prev"
Say "Starting the station"
Start-Process -FilePath $Exe -WorkingDirectory $App
exit 0
