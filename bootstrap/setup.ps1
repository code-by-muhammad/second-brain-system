# Second Brain: one-command setup for Windows (ordinary PowerShell, not Git Bash).
# Downloads one exact release, checks that it is the reviewed one, then runs its setup.
# Extra arguments go to setup_device.py, for example: .\setup.ps1 --yes
$ErrorActionPreference = 'Stop'

$Tag = if ($env:SECOND_BRAIN_REF) { $env:SECOND_BRAIN_REF } else { 'v0.2.6' }
$Commit = if ($env:SECOND_BRAIN_COMMIT) { $env:SECOND_BRAIN_COMMIT } else { '__COMMIT__' }
$Root = if ($env:SECOND_BRAIN_ROOT) { $env:SECOND_BRAIN_ROOT } else { 'C:\Second-Brain-System' }
$Source = 'https://github.com/code-by-muhammad/second-brain-system.git'

if ($Commit -notmatch '^[0-9a-f]{40,64}$') {
  throw 'This copy of setup.ps1 is the source file and names no release commit. Use the setup.ps1 attached to the release.'
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "Missing: git. Run 'winget install --id Git.Git -e' and open a new PowerShell window."
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "Missing: uv. Run 'winget install --id astral-sh.uv -e' and open a new PowerShell window."
}

$Dest = Join-Path (Join-Path $Root 'releases') $Tag
if (-not (Test-Path -LiteralPath (Join-Path $Dest '.git'))) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Root 'releases') | Out-Null
  git clone -q -c core.autocrlf=false --branch $Tag --single-branch $Source $Dest
  if ($LASTEXITCODE -ne 0) { throw 'The release could not be downloaded.' }
}
$Head = (git -C $Dest rev-parse HEAD).Trim()
if ($Head -ne $Commit) {
  throw "Release check failed: expected commit $Commit, found $Head in $Dest. Nothing was installed. Keep that folder for review."
}
# A release folder kept from an earlier run must still be exactly that commit before any of it runs:
# no extra or changed file by Git's own view, and every tracked file's bytes equal to its committed blob
# (hashed here, so a flag in the folder's index cannot hide a change).
$Extra = git -C $Dest -c core.fsmonitor=false status --porcelain
$Failed = $LASTEXITCODE -ne 0
$Want = @(git -C $Dest ls-tree -r HEAD | ForEach-Object { ($_ -split '\s+')[2] })
$Failed = $Failed -or $LASTEXITCODE -ne 0
$Have = @(git -C $Dest ls-tree -r --name-only HEAD | git -C $Dest hash-object --no-filters --stdin-paths)
$Failed = $Failed -or $LASTEXITCODE -ne 0
if ($Failed -or $Extra -or $Want.Count -eq 0 -or (($Want -join ' ') -ne ($Have -join ' '))) {
  throw "Release check failed: files in $Dest differ from commit $Commit. Nothing was run. Keep that folder for review, or move it away and run this again."
}
Set-Location -LiteralPath $Dest
uv run --no-project --python 3.12 python -I -B setup_device.py --root $Root @args
exit $LASTEXITCODE
