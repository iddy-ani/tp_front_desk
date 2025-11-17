# Script to copy files from network drive to local working directory
# Source: Network drive (read-only)
# Destination: Test Programs folder in working directory

param(
    [string]$SourcePath = "\\alpfile3.al.intel.com\sdx_ods\program\1278\prod\hdmtprogs\ptl_ptu_sds\PTUSDJXA1H21G402546",
    [string]$DestinationPath = "Test Programs"
)

# Get the script's directory (working directory)
$WorkingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $WorkingDir) {
    $WorkingDir = Get-Location
}

# Create full destination path
$FullDestination = Join-Path -Path $WorkingDir -ChildPath $DestinationPath

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
Write-Host "Starting copy operation..." -ForegroundColor Green

try {
    # Use Robocopy for efficient, multi-threaded copying
    # /E = Copy subdirectories, including empty ones
    # /MT:16 = Multi-threaded (16 threads for speed)
    # /R:2 = Retry 2 times on failed copies
    # /W:5 = Wait 5 seconds between retries
    # /NP = No progress percentage (cleaner output)
    # /NDL = No directory list (cleaner output)
    # /NFL = No file list (cleaner output, remove this if you want to see each file)
    # /LOG = Optional: create a log file
    
    $RobocopyArgs = @(
        "`"$SourcePath`"",
        "`"$FullDestination`"",
        "*.*",
        "/E",
        "/MT:16",
        "/R:2",
        "/W:5",
        "/NP"
    )
    
    Write-Host "Using Robocopy with 16 threads for optimal speed..." -ForegroundColor Yellow
    
    $Result = Start-Process -FilePath "robocopy" -ArgumentList $RobocopyArgs -Wait -PassThru -NoNewWindow
    
    # Robocopy exit codes: 0-7 are success (0-3 = no errors, 4-7 = some files not copied but not critical)
    if ($Result.ExitCode -le 7) {
        $EndTime = Get-Date
        $Duration = $EndTime - $StartTime
        
        Write-Host ""
        Write-Host "Copy completed successfully!" -ForegroundColor Green
        Write-Host "Time taken: $($Duration.ToString('mm\:ss'))" -ForegroundColor Cyan
        Write-Host "Files copied to: $FullDestination" -ForegroundColor Cyan
        
        # Get folder size
        $Size = (Get-ChildItem -Path $FullDestination -Recurse -File | Measure-Object -Property Length -Sum).Sum
        $SizeMB = [math]::Round($Size / 1MB, 2)
        Write-Host "Total size: $SizeMB MB" -ForegroundColor Cyan
    }
    else {
        Write-Host ""
        Write-Host "WARNING: Copy completed with exit code $($Result.ExitCode)" -ForegroundColor Yellow
        Write-Host "Some files may not have been copied. Check the destination folder." -ForegroundColor Yellow
    }
}
catch {
    Write-Host ""
    Write-Host "ERROR: An error occurred during the copy operation." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
