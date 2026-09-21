#Requires -Version 5.1
<#
.SYNOPSIS
    MLB-300 Tier 1: nightly snapshot of the almanac checkout's untracked jewels.

.DESCRIPTION
    Git protects only what is committed and pushed. This project keeps its most
    valuable files OUT of git on purpose (CLAUDE.md, Study Material.MD,
    BRAINTHOUGHTS.md, the real league_config/, archives/anonymization/, the
    goldens under tests/fixtures/, everything under scratchpad/, the raw parquet
    dump, and the Fantrax capture corpus that lives outside the repo). This
    script is the automatic, versioned safety net for those files. In order:

      1. git bundle --all            -> <BackupRoot>\bundles\almanac-<date>.bundle   keep 7
      2. robocopy /E /XO the jewels  -> <BackupRoot>\daily\<date>\                   keep 14
      3. Mondays only: raw parquet   -> <BackupRoot>\weekly-raw\<date>\              keep 4
      4. git push dev main:main-public, ONLY if the tree has no tracked changes and
         main is strictly ahead of dev/main-public (never --force, never --tags)
      5. Tier 3 private-assets sync, if that working tree exists on this machine
      6. one-line summary

    THIS SCRIPT NEVER DELETES. Retention MOVES expired snapshots into an
    _expired\ sibling folder that Kyle empties by hand. It never uses
    robocopy /MIR or /PURGE. Credentials (.env, cookies, tokens, secrets, keys)
    are regenerable and are excluded from every copy by ruling.

    Restore: git clone <bundle>  for the repo; plain copy for everything else.

    DESTINATION NOTE (2026-09-21): the task was registered with
    -BackupRoot C:\Users\kyled\Backups\almanac (plain local folder) because the
    OneDrive client was not running. To move it back onto OneDrive, edit the
    scheduled task's action argument and drop the -BackupRoot switch so the
    default below applies:
      $t = Get-ScheduledTask AlmanacBackup-MLB300
      $t.Actions[0].Arguments = $t.Actions[0].Arguments -replace ' -BackupRoot \S+', ''
      Set-ScheduledTask -TaskName AlmanacBackup-MLB300 -Action $t.Actions
    Every run logs the root in use on its first line.

.PARAMETER BackupRoot
    Where bundles\, daily\, weekly-raw\ and logs\ live. Default: the OneDrive
    backup folder. Off-machine coverage while it points at a local folder comes
    from Tier 3 (the private-assets repo) and the private GitHub Release.
.PARAMETER ForceWeekly
    Take the weekly raw-parquet snapshot today regardless of weekday.
.PARAMETER NoPush
    Skip step 4 (useful for a dry run with Kyle watching).
.PARAMETER NoPrivateSync
    Skip step 5.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\backup_snapshot.ps1 -NoPush
#>
[CmdletBinding()]
param(
    [string]$Checkout       = 'C:\Users\kyled\projects\espn-league-manager',
    [string]$BackupRoot     = 'C:\Users\kyled\OneDrive\Backups\almanac',
    [string]$FantraxCapture = 'C:\Users\kyled\fantrax-capture',
    [string]$PrivateAssets  = 'C:\Users\kyled\projects\almanac-private-assets',
    [int]$KeepBundles   = 7,
    [int]$KeepDaily     = 14,
    [int]$KeepWeeklyRaw = 4,
    [int]$MaxFileMB     = 200,
    # Dated: fantrax-capture is copied into every dated daily folder (the
    # kickoff design; ~700 MB x 14). Rolling: one add-only mirror at
    # <BackupRoot>\fantrax-capture\ refreshed nightly with /XO, so the daily
    # folders stay small and expiry churn drops by ~20 GB a month.
    [ValidateSet('Dated', 'Rolling')]
    [string]$FantraxMode = 'Dated',
    [switch]$ForceWeekly,
    [switch]$NoPush,
    [switch]$NoPrivateSync
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Continue'
$env:GIT_TERMINAL_PROMPT = '0'      # fail fast instead of hanging on a credential prompt
$env:GCM_INTERACTIVE    = 'never'

$Date      = Get-Date -Format 'yyyy-MM-dd'
$Started   = Get-Date
$LogDir    = Join-Path $BackupRoot 'logs'
$BundleDir = Join-Path $BackupRoot 'bundles'
$DailyRoot = Join-Path $BackupRoot 'daily'
$DailyDir  = Join-Path $DailyRoot $Date
$RawRoot   = Join-Path $BackupRoot 'weekly-raw'
$RawDir    = Join-Path $RawRoot $Date
foreach ($d in @($LogDir, $BundleDir, $DailyRoot, $RawRoot)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
$LogFile = Join-Path $LogDir "$Date.log"

$script:Failures = New-Object System.Collections.ArrayList

function Write-Log {
    param([string]$Message)
    $line = '{0} {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}
function Add-Failure {
    param([string]$Step, [string]$Detail)
    [void]$script:Failures.Add("$Step -- $Detail")
    Write-Log "FAIL $Step -- $Detail"
}
function Get-FolderStats {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @{ MB = 0; Files = 0 } }
    $m = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
         Measure-Object Length -Sum
    return @{ MB = [math]::Round(($m.Sum / 1MB), 1); Files = [int]$m.Count }
}
function Get-FreeGB { return [math]::Round((Get-PSDrive C).Free / 1GB, 1) }

# Names that are regenerable secrets. Excluded from EVERY copy, by ruling.
$CredentialExcludes = @('.env', '*.env', '*cookies*', '*token*', '*secret*', '*.pem', '*.key')

function Invoke-Robocopy {
    <#
      Wraps robocopy with the house flags. Never /MIR, never /PURGE: the
      destination is only ever added to. Exit codes 0-7 are success variants
      (bits: 1 copied, 2 extras on dest, 4 mismatches); 8 and above mean
      something failed to copy.
    #>
    param(
        [string]$Label,
        [string]$Source,
        [string]$Dest,
        [string[]]$Files = @('*.*'),
        [switch]$Recurse,
        [string[]]$ExcludeFiles = @(),
        [string[]]$ExcludeDirs = @(),
        [int]$MaxMB = 0
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        Add-Failure $Label "source missing: $Source"
        return
    }
    $rcArgs = @($Source, $Dest) + $Files
    if ($Recurse) { $rcArgs += '/E' }
    $rcArgs += @('/R:1', '/W:1', '/XO', '/XJ', '/NP', '/NDL', '/NFL', '/NJH')
    $rcArgs += @('/XF') + $CredentialExcludes + $ExcludeFiles
    if ($ExcludeDirs.Count -gt 0) { $rcArgs += @('/XD') + $ExcludeDirs }
    if ($MaxMB -gt 0) { $rcArgs += ('/MAX:{0}' -f ($MaxMB * 1MB)) }

    $out = & robocopy.exe @rcArgs
    $rc  = $LASTEXITCODE
    $summary = ($out | Where-Object { $_ -match '^\s+(Dirs|Files|Bytes)\s*:' }) -replace '\s+', ' '
    if ($rc -ge 8) {
        Add-Failure $Label "robocopy rc=$rc; $($summary -join ' | ')"
        $out | Where-Object { $_ -match 'ERROR' } | Select-Object -First 10 | ForEach-Object { Write-Log "    $_" }
    } else {
        Write-Log ('  {0}: ok (rc={1}) {2}' -f $Label, $rc, ($summary -join ' | '))
    }
}

function Move-Expired {
    <#
      Retention without deletion: everything past the newest $Keep items
      (by name; names are date-stamped so name order is age order) moves
      into <Dir>\_expired\. Kyle empties _expired by hand, monthly.
    #>
    param([string]$Dir, [string]$Pattern, [int]$Keep, [string]$Label)
    if (-not (Test-Path -LiteralPath $Dir)) { return }
    $items = @(Get-ChildItem -LiteralPath $Dir -Force |
               Where-Object { $_.Name -match $Pattern } |
               Sort-Object Name -Descending)
    if ($items.Count -le $Keep) {
        Write-Log ('  retention {0}: {1}/{2} kept, nothing expired' -f $Label, $items.Count, $Keep)
        return
    }
    $expiredDir = Join-Path $Dir '_expired'
    New-Item -ItemType Directory -Force -Path $expiredDir | Out-Null
    foreach ($item in ($items | Select-Object -Skip $Keep)) {
        $target = Join-Path $expiredDir $item.Name
        if (Test-Path -LiteralPath $target) { $target = '{0}.dup-{1}' -f $target, (Get-Date -Format 'HHmmss') }
        try {
            Move-Item -LiteralPath $item.FullName -Destination $target -ErrorAction Stop
            Write-Log ('  retention {0}: moved {1} -> _expired\' -f $Label, $item.Name)
        } catch {
            Add-Failure "retention $Label" "could not move $($item.Name): $($_.Exception.Message)"
        }
    }
}

# ---------------------------------------------------------------------------
$FreeBefore = Get-FreeGB
Write-Log "=== backup_snapshot start $Date (MLB-300 Tier 1); BackupRoot=$BackupRoot; FantraxMode=$FantraxMode; C: free ${FreeBefore} GB"

$Git = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
if (-not $Git) { $Git = 'C:\Program Files\Git\cmd\git.exe' }
if (-not (Test-Path -LiteralPath $Git)) { Add-Failure 'git' "git.exe not found at $Git" }
if (-not (Test-Path -LiteralPath $Checkout)) {
    Add-Failure 'checkout' "missing: $Checkout"
    Write-Log 'aborting: nothing to back up'
    exit 2
}

# 1. Bundle every ref (unpushed commits included). Restore: git clone <bundle>.
Write-Log '[1/6] git bundle'
$BundlePath = Join-Path $BundleDir "almanac-$Date.bundle"
$BundleMB = 0
if ($script:Failures.Count -eq 0) {
    Push-Location $Checkout
    try {
        $out = & $Git bundle create $BundlePath --all --quiet 2>&1 | ForEach-Object { "$_" }
        $rc = $LASTEXITCODE
    } finally { Pop-Location }
    if ($rc -ne 0 -or -not (Test-Path -LiteralPath $BundlePath)) {
        Add-Failure 'bundle' ("rc=$rc " + ($out -join ' '))
    } else {
        $BundleMB = [math]::Round((Get-Item -LiteralPath $BundlePath).Length / 1MB, 1)
        Write-Log "  bundle: $BundlePath (${BundleMB} MB)"
    }
    Move-Expired -Dir $BundleDir -Pattern '^almanac-\d{4}-\d{2}-\d{2}\.bundle$' -Keep $KeepBundles -Label 'bundles'
}

# 2. The jewels, into a dated folder. Never /MIR: the folder only gains files.
Write-Log "[2/6] daily copy -> $DailyDir"
New-Item -ItemType Directory -Force -Path $DailyDir | Out-Null

Invoke-Robocopy -Label 'root notes' -Source $Checkout -Dest $DailyDir `
    -Files @('CLAUDE.md', 'Study Material.MD', 'BRAINTHOUGHTS.md')

Invoke-Robocopy -Label 'league_config (real)' -Recurse `
    -Source (Join-Path $Checkout 'dbt_league\league_config') `
    -Dest   (Join-Path $DailyDir 'dbt_league\league_config')

Invoke-Robocopy -Label 'archives/anonymization' -Recurse `
    -Source (Join-Path $Checkout 'archives\anonymization') `
    -Dest   (Join-Path $DailyDir 'archives\anonymization')

# The byte-diff goldens: tests/fixtures/{almanac_v1_1_0,cbs_almanac,points_almanac_v2_1}
# plus the two baseline_*.txt, all gitignored (see .gitignore lines 130-134).
Invoke-Robocopy -Label 'goldens (tests/fixtures)' -Recurse `
    -Source (Join-Path $Checkout 'tests\fixtures') `
    -Dest   (Join-Path $DailyDir 'tests\fixtures')

# scratchpad: handoffs, kickoffs, ledgers, RELEASING.md. The warehouse copies
# that experiments leave here (*.duckdb, *.parquet, *.tar; 2.3 GB on 09-21)
# are excluded by extension, and anything over the size cap is excluded.
Invoke-Robocopy -Label 'scratchpad' -Recurse `
    -Source (Join-Path $Checkout 'scratchpad') `
    -Dest   (Join-Path $DailyDir 'scratchpad') `
    -ExcludeFiles @('*.tar', '*.duckdb', '*.parquet') `
    -ExcludeDirs  @('_to_delete', 'node_modules', '__pycache__') `
    -MaxMB $MaxFileMB

# The Fantrax capture harness lives OUTSIDE the repo; its corpus/ is
# irreplaceable. The venv and bytecode are regenerable.
if ($FantraxMode -eq 'Rolling') {
    $FantraxDest = Join-Path $BackupRoot 'fantrax-capture'
} else {
    $FantraxDest = Join-Path $DailyDir 'fantrax-capture'
}
Invoke-Robocopy -Label "fantrax-capture ($FantraxMode)" -Recurse `
    -Source $FantraxCapture `
    -Dest   $FantraxDest `
    -ExcludeDirs @('.venv', '__pycache__') `
    -MaxMB $MaxFileMB

$DailyStats = Get-FolderStats $DailyDir
Write-Log ('  daily folder: {0} MB, {1} files' -f $DailyStats.MB, $DailyStats.Files)
if ($FantraxMode -eq 'Rolling') {
    $FxStats = Get-FolderStats $FantraxDest
    Write-Log ('  fantrax-capture rolling mirror: {0} MB, {1} files' -f $FxStats.MB, $FxStats.Files)
}
Move-Expired -Dir $DailyRoot -Pattern '^\d{4}-\d{2}-\d{2}$' -Keep $KeepDaily -Label 'daily'

# 3. Weekly: the raw parquet dump (tools/dump_snowflake_raw_to_parquet.py writes
#    data\parquet\raw; ~365 MB). Mondays, after Kyle's Sunday update.
$IsWeekly = ($ForceWeekly -or ((Get-Date).DayOfWeek -eq [DayOfWeek]::Monday))
$RawMB = 'skipped (not Monday)'
if ($IsWeekly) {
    Write-Log "[3/6] weekly raw parquet -> $RawDir"
    Invoke-Robocopy -Label 'weekly-raw' -Recurse `
        -Source (Join-Path $Checkout 'data\parquet\raw') `
        -Dest   $RawDir
    $RawMB = '{0} MB' -f (Get-FolderStats $RawDir).MB
    Move-Expired -Dir $RawRoot -Pattern '^\d{4}-\d{2}-\d{2}$' -Keep $KeepWeeklyRaw -Label 'weekly-raw'
} else {
    Write-Log '[3/6] weekly raw parquet: skipped (not Monday; use -ForceWeekly to override)'
}

# 4. Mirror main to the private dev repo. Guards: no tracked changes in the
#    working tree; main strictly ahead of dev/main-public; fast-forward only.
#    dev/main (no suffix) is the frozen pre-rewrite backup and is never touched.
Write-Log '[4/6] git push dev main:main-public'
$PushResult = 'not attempted'
if ($NoPush) {
    $PushResult = 'skipped (-NoPush)'
    Write-Log "  push: $PushResult"
} elseif ($script:Failures.Count -gt 0 -and -not (Test-Path -LiteralPath $Git)) {
    $PushResult = 'skipped (git missing)'
} else {
    Push-Location $Checkout
    try {
        $dirty = @(& $Git status --porcelain --untracked-files=no 2>&1 | ForEach-Object { "$_" })
        if ($dirty.Count -gt 0) {
            $PushResult = "skipped (tracked changes present: $($dirty.Count) paths)"
        } else {
            # Refresh the remote-tracking ref so the comparison is honest. The
            # remote is configured tagOpt=--no-tags; --no-tags here is belt and braces.
            $fetchOut = & $Git fetch dev main-public --no-tags --quiet 2>&1 | ForEach-Object { "$_" }
            if ($LASTEXITCODE -ne 0) { Write-Log ('  fetch dev main-public failed (continuing on the local ref): ' + ($fetchOut -join ' ')) }
            $ahead  = [int](& $Git rev-list --count dev/main-public..main 2>$null)
            $behind = [int](& $Git rev-list --count main..dev/main-public 2>$null)
            if ($ahead -eq 0) {
                $PushResult = 'skipped (nothing to push; main == dev/main-public)'
            } elseif ($behind -ne 0) {
                $PushResult = "REFUSED (dev/main-public has $behind commits not on main; a push would not fast-forward and --force is forbidden)"
                Add-Failure 'push' $PushResult
            } else {
                $pushOut = & $Git push dev main:main-public 2>&1 | ForEach-Object { "$_" }
                if ($LASTEXITCODE -eq 0) {
                    $PushResult = "pushed $ahead commit(s)"
                } else {
                    $PushResult = 'FAILED: ' + (($pushOut | Select-Object -Last 3) -join ' ')
                    Add-Failure 'push' $PushResult
                }
            }
        }
    } finally { Pop-Location }
    Write-Log "  push: $PushResult"
}

# 5. Tier 3: versioned mirror of the same jewels in the private-assets repo.
Write-Log '[5/6] private-assets sync'
$SyncResult = 'not configured'
$SyncScript = Join-Path $PrivateAssets 'sync_from_checkout.ps1'
if ($NoPrivateSync) {
    $SyncResult = 'skipped (-NoPrivateSync)'
} elseif (Test-Path -LiteralPath $SyncScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SyncScript `
        -Checkout $Checkout -FantraxCapture $FantraxCapture -LogFile $LogFile -MaxFileMB $MaxFileMB
    if ($LASTEXITCODE -eq 0) { $SyncResult = 'ok' } else { $SyncResult = "FAILED rc=$LASTEXITCODE"; Add-Failure 'private-sync' $SyncResult }
} else {
    Write-Log "  private-assets working tree not present ($SyncScript); skipping"
}
Write-Log "  private-sync: $SyncResult"

# 6. Summary
$FreeAfter = Get-FreeGB
$Elapsed   = [math]::Round(((Get-Date) - $Started).TotalMinutes, 1)
$Summary = ('SUMMARY {0} | bundle {1} MB | daily {2} MB, {3} files | weekly-raw {4} | push: {5} | private-sync: {6} | C: free {7} -> {8} GB | {9} min | failures: {10}' -f `
    $Date, $BundleMB, $DailyStats.MB, $DailyStats.Files, $RawMB, $PushResult, $SyncResult, $FreeBefore, $FreeAfter, $Elapsed, $script:Failures.Count)
Write-Log "[6/6] $Summary"
if ($script:Failures.Count -gt 0) {
    $script:Failures | ForEach-Object { Write-Log "  failure: $_" }
    exit 1
}
exit 0
