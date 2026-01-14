# Script to copy files from network drive to local working directory
# Source: Network drive (read-only)
# Destination: Test Programs folder in working directory

param(
    [string]$SourcePath = "\\alpfile3.al.intel.com\sdx_ods\program\1278\prod\hdmtprogs\ptl_ptu_sds\PTUSDJXA1H21G402546",
    [string]$DestinationPath = "Test Programs"
)

# Resolve destination path.
# - If DestinationPath is absolute, use it directly.
# - Otherwise, treat it as relative to the script directory.
$WorkingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $WorkingDir) { $WorkingDir = (Get-Location).Path }

$FullDestination = $DestinationPath
if (-not [System.IO.Path]::IsPathRooted($DestinationPath)) {
    $FullDestination = Join-Path -Path $WorkingDir -ChildPath $DestinationPath
}

Write-Host "Source: $SourcePath" -ForegroundColor Cyan
Write-Host "Destination: $FullDestination" -ForegroundColor Cyan
Write-Host ""

# Check if source exists
if (-not (Test-Path -Path $SourcePath)) {
    Write-Host "ERROR: Source path does not exist or is not accessible." -ForegroundColor Red
    Write-Host "Path: $SourcePath" -ForegroundColor Red
    exit 1
}

# Create destination folder if it doesn't exist
if (-not (Test-Path -Path $FullDestination)) {
    Write-Host "Creating destination folder..." -ForegroundColor Yellow
    New-Item -Path $FullDestination -ItemType Directory -Force | Out-Null
}

# Start timing
$StartTime = Get-Date
Write-Host "Starting copy operation (ingestion artifacts only)..." -ForegroundColor Green

try {
    # We only need a small subset of the TP for ingestion:
    # - Reports (csv/txt)
    # - Root metadata files (BaseLevels/BaseSpecs/EnvironmentFile)
    # - Module templates (*.mtpl) for SetPoints parsing
    # - HVQK config JSONs (*.hvqk.config.json)
    # Copying the entire TP (patterns, binaries, etc.) is unnecessarily expensive.

    $CommonArgs = @(
        "/MT:16",
        "/R:2",
        "/W:5",
        "/NP",
        "/NDL",
        "/NFL"
    )

    function Invoke-Robocopy {
        param(
            [Parameter(Mandatory=$true)][string]$Src,
            [Parameter(Mandatory=$true)][string]$Dst,
            [Parameter(Mandatory=$true)][string[]]$Files,
            [Parameter(Mandatory=$false)][string[]]$ExtraArgs = @()
        )

        if (-not (Test-Path -Path $Src)) {
            return 0
        }
        if (-not (Test-Path -Path $Dst)) {
            New-Item -Path $Dst -ItemType Directory -Force | Out-Null
        }

        & robocopy $Src $Dst @Files @ExtraArgs @CommonArgs | Out-Null
        return $LASTEXITCODE
    }

    $ExitCodes = @()

    Write-Host "Copying Reports/..." -ForegroundColor Yellow
    $ReportsSrc = Join-Path -Path $SourcePath -ChildPath "Reports"
    $ReportsDst = Join-Path -Path $FullDestination -ChildPath "Reports"
    $ExitCodes += Invoke-Robocopy -Src $ReportsSrc -Dst $ReportsDst -Files @("*.*") -ExtraArgs @("/E")

    Write-Host "Copying root metadata files..." -ForegroundColor Yellow
    $ExitCodes += Invoke-Robocopy -Src $SourcePath -Dst $FullDestination -Files @(
        "BaseLevels.tcg",
        "BaseSpecs.usrv",
        "EnvironmentFile.env"
    ) -ExtraArgs @()

    Write-Host "Copying Modules/**/*.mtpl and HVQK configs..." -ForegroundColor Yellow
    $ModulesSrc = Join-Path -Path $SourcePath -ChildPath "Modules"
    $ModulesDst = Join-Path -Path $FullDestination -ChildPath "Modules"
    $ExitCodes += Invoke-Robocopy -Src $ModulesSrc -Dst $ModulesDst -Files @(
        "*.mtpl",
        "*.hvqk.config.json"
    ) -ExtraArgs @("/S")

    $WorstExit = ($ExitCodes | Measure-Object -Maximum).Maximum

    # Robocopy exit codes: 0-7 are success (0-3 = no errors, 4-7 = some files skipped but not critical)
    if ($WorstExit -le 7) {
        $EndTime = Get-Date
        $Duration = $EndTime - $StartTime

        Write-Host ""
        Write-Host "Copy completed successfully!" -ForegroundColor Green
        Write-Host "Time taken: $($Duration.ToString('mm\:ss'))" -ForegroundColor Cyan
        Write-Host "Files copied to: $FullDestination" -ForegroundColor Cyan
        exit 0
    }
    else {
        Write-Host ""
        Write-Host "ERROR: Copy completed with robocopy exit code $WorstExit" -ForegroundColor Red
        exit 2
    }
}
catch {
    Write-Host ""
    Write-Host "ERROR: An error occurred during the copy operation." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
