[CmdletBinding()]
param(
    [ValidateSet("standalone", "onefile")]
    [string]$Mode = "standalone",

    [ValidateSet("attach", "disable")]
    [string]$ConsoleMode = "attach",

    [switch]$SkipInstall,

    [switch]$PreserveBuildCache
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DesktopDir = $PSScriptRoot
$ProjectDir = (Resolve-Path (Join-Path $DesktopDir "..")).Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$BuildDir = Join-Path $DesktopDir "build"
$Launcher = Join-Path $DesktopDir "launcher.py"
$DataDir = Join-Path $ProjectDir "data"

# Only the files the running app actually opens are bundled. The rest of data/
# (per-province boundary snapshots, per-kabupaten CSV splits, scrape outputs,
# backups) are rebuild-time inputs and would only bloat the executable.
$RuntimeDataFiles = @(
    "gempa_master.csv",
    "gempa_working.sqlite",
    "provinsi_indonesia_38.geojson",
    "kabupaten_kota_indonesia.geojson",
    "kecamatan_lightweight.geojson",
    "kabupaten_pesisir.json",
    "kepadatan_penduduk_kecamatan.csv",
    "stasiun_seismik.csv",
    "sesar_pusgen_2024.geojson",
    "krb_gunung_api_indonesia_big.geojson",
    "sum_slab2_dep_contours.in",
    "indonesia_trench_slab2.geojson",
    "LICENSE-GeoJson-Indonesia-38-Provinsi.txt",
    "README.md"
)

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment tidak ditemukan: $Python"
}

if (-not $SkipInstall) {
    $Uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $Uv) {
        & $Uv.Source pip install --python $Python --system-certs nuitka ordered-set zstandard
    }
    else {
        & $Python -m pip install nuitka ordered-set zstandard
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Instalasi Nuitka gagal."
    }
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

$NuitkaArgs = @(
    "-m", "nuitka",
    "--mode=$Mode",
    "--assume-yes-for-downloads",
    "--output-dir=$BuildDir",
    "--output-filename=E-QuakeVis.exe",
    "--windows-console-mode=$ConsoleMode",
    "--product-name=E-QuakeVis",
    "--file-description=E-QuakeVis",
    "--file-version=1.0.0.0",
    "--product-version=1.0.0.0",
    "--copyright=Copyright (c) 2026 E-QuakeVis",
    "--include-module=desktop_entry",
    "--include-module=app",
    "--include-package=utils",
    "--include-package=openpyxl",
    "--include-package=PIL",
    "--include-package=mapbox_vector_tile",
    "--include-package=plotly",
    "--include-package-data=plotly",
    "--include-data-dir=$ProjectDir/utils/seismic_view=utils/seismic_view",
    "--include-data-dir=$DataDir/slab2=data/slab2",
    "--include-package-data=streamlit",
    "--include-package-data=streamlit_folium",
    "--include-package-data=folium",
    "--include-package-data=branca"
)

if (-not $PreserveBuildCache) {
    $NuitkaArgs += "--remove-output"
}

foreach ($DataFile in $RuntimeDataFiles) {
    $Source = Join-Path $DataDir $DataFile
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Berkas data runtime tidak ditemukan: $Source"
    }
    $NuitkaArgs += "--include-data-files=$Source=data/$DataFile"
}

if ($Mode -eq "onefile") {
    $NuitkaArgs += '--onefile-tempdir-spec={CACHE_DIR}/E-QuakeVis/1.0'
}

$NuitkaArgs += $Launcher

Push-Location $ProjectDir
try {
    & $Python @NuitkaArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Build Nuitka gagal dengan exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

if ($Mode -eq "standalone") {
    $Result = Join-Path $BuildDir "launcher.dist\E-QuakeVis.exe"
}
else {
    $Result = Join-Path $BuildDir "E-QuakeVis.exe"
}

Write-Host "Build selesai: $Result"
