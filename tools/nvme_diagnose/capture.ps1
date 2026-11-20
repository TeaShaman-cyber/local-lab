param([Parameter(Mandatory=$true)][string]$RunDir)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($RunDir)
if ($root -notmatch '^E:\\diagnostics\\nvme-274\\[0-9A-Za-z-]+$') { throw 'Unexpected output directory.' }
if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'Output directory must already exist.' }
$wpr = Join-Path $env:SystemRoot 'System32\wpr.exe'
$log = Join-Path $root 'capture.log'
$etl = Join-Path $root 'disk.etl'
$profile = (Join-Path $PSScriptRoot 'nvme-disk.wprp') + '!NvmeDisk'
$session = 'Nvme274-' + (Split-Path $root -Leaf)
function Note([string]$Message) { Add-Content -LiteralPath $log -Value (([DateTimeOffset]::UtcNow.ToString('o')) + ' ' + $Message) -Encoding UTF8 }
function Invoke-Wpr([string[]]$Argv) {
    & $wpr @Argv -instancename $session | ForEach-Object { Note $_ }
    return $LASTEXITCODE
}
$active = $false
try {
    Note 'START_REQUEST'
    $rc = Invoke-Wpr @('-start', $profile, '-recordtempto', $root)
    if ($rc -ne 0) { throw "WPR start exit $rc" }
    $active = $true
    Note 'CAPTURE_STARTED'
    Start-Sleep -Seconds 16
    $null = Invoke-Wpr @('-status')
    Note 'STOP_REQUEST'
    $rc = Invoke-Wpr @('-stop', $etl, 'NVMe disk attribution 274', '-skipPdbGen')
    if ($rc -ne 0) { throw "WPR stop exit $rc" }
    $active = $false
    $len = (Get-Item -LiteralPath $etl).Length
    if ($len -lt 4096) { throw "Unexpected ETL size $len" }
    Note "CAPTURE_COMPLETE bytes=$len"
} catch {
    Note ('ERROR ' + $_.Exception.Message)
    exit 1
} finally {
    if ($active) { $rc = Invoke-Wpr @('-cancel'); Note "CANCEL_OWN_SESSION exit=$rc" }
}
