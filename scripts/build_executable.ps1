# Build the Windows operator bundle: dist\CamShaft_Inspection_Station\  (+ zip)
# Run on Windows from the project root:  .\scripts\build_executable.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$AppName = "CamShaft_Inspection_Station"
# Output folder (default dist). Use another if a station is running from dist:
#   $env:BUILD_DIST = "release"; .\scripts\build_executable.ps1
$DistName = if ($env:BUILD_DIST) { $env:BUILD_DIST } else { "dist" }
$DistDir = Join-Path $Root $DistName
$AppDir = Join-Path $DistDir $AppName
$ExePath = Join-Path $AppDir "$AppName.exe"
$ZipPath = Join-Path $DistDir "$AppName-win64.zip"

Write-Host "==> Building frontend (Next.js static export)"
Push-Location frontend
if (-not (Test-Path node_modules)) { npm install }
npm run build
Pop-Location
if (-not (Test-Path "$Root\frontend\out\index.html")) { throw "frontend\out\index.html missing" }

Write-Host "==> Syncing Python dependencies (with build group)"
uv sync --group build

if (-not (Test-Path "$Root\models\thread_classifier.joblib")) {
    Write-Host "==> Training thread model (models\thread_classifier.joblib missing)"
    uv run python -c "from src.core import config; from src.inspection.thread_classifier import ThreadClassifier as T; T.train(config.DATA_DIR).save(config.MODEL_SAVE_PATH)"
}

Write-Host "==> Running PyInstaller (onedir)"
uv run pyinstaller packaging/camshaft_station.spec --noconfirm `
    --workpath "$Root\build\pyinstaller" `
    --distpath "$DistDir"
if (-not (Test-Path $ExePath)) { throw "Missing $ExePath" }

# Ship the training images so the model can be retrained on site (API).
New-Item -ItemType Directory -Force "$AppDir\data" | Out-Null
Copy-Item -Recurse -Force "$Root\data\Thread" "$AppDir\data\Thread"
Copy-Item -Recurse -Force "$Root\data\No-Thread" "$AppDir\data\No-Thread"
Copy-Item -Force "$Root\packaging\OPERATOR_README.txt" "$AppDir\README.txt"

Write-Host "==> Creating release zip"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $AppDir -DestinationPath $ZipPath -Force

$zipMb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "Done."
Write-Host "  Run:  $ExePath"
Write-Host "  Zip:  $ZipPath ($zipMb MB)"
Write-Host "Keep the whole $AppName folder together (_internal is required)."
