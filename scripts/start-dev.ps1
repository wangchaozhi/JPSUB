param(
    [string]$CudaPath = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1',
    [string]$FfmpegBin = 'E:\dev\ffmpeg-master-latest-win64-gpl-shared\bin'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$tauriDir = Join-Path $repoRoot 'shell\src-tauri'
$sidecar = Join-Path $tauriDir 'binaries\engine-x86_64-pc-windows-msvc.exe'

if (!(Test-Path $sidecar)) {
    throw "Missing sidecar: $sidecar. Build engine with PyInstaller first."
}

$cudaBin = Join-Path $CudaPath 'bin'
if (Test-Path $cudaBin) {
    $env:CUDA_PATH = $CudaPath
    $env:PATH = $cudaBin + ';' + $env:PATH
    Write-Host 'CUDA:' $CudaPath
} else {
    Write-Host 'CUDA path not found; engine may fall back to CPU:' $CudaPath
}

if (Test-Path $FfmpegBin) {
    $env:JPSUB_FFMPEG = Join-Path $FfmpegBin 'ffmpeg.exe'
    $env:JPSUB_FFPROBE = Join-Path $FfmpegBin 'ffprobe.exe'
    $env:PATH = $FfmpegBin + ';' + $env:PATH
    Write-Host 'FFmpeg:' $FfmpegBin
}

Push-Location $tauriDir
try {
    cargo tauri dev
} finally {
    Pop-Location
}
