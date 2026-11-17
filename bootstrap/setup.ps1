# Second Brain: one-command setup for Windows (ordinary PowerShell, not Git Bash).
# Downloads one exact release, checks that it is the reviewed one, then runs its setup.
# Extra arguments go to setup_device.py, for example: .\setup.ps1 --yes
$ErrorActionPreference = 'Stop'

$Tag = if ($env:SECOND_BRAIN_REF) { $env:SECOND_BRAIN_REF } else { 'v0.2.11' }
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

# Every file below a folder with its SHA256, without the folder's own Python environments and Git folder.
function Get-Listing([string]$Folder) {
  $Base = (Resolve-Path -LiteralPath $Folder).Path.TrimEnd('\')
  Get-ChildItem -LiteralPath $Base -Recurse -File -Force |
    ForEach-Object { $_.FullName.Substring($Base.Length) } |
    Where-Object { $_ -notmatch '\\(\.git|\.venv[^\\]*)\\' } |
    Sort-Object |
    ForEach-Object { $_ + ' ' + (Get-FileHash -LiteralPath ($Base + $_) -Algorithm SHA256).Hash }
}

# Always download fresh, and trust only that download.
$Dest = Join-Path (Join-Path $Root 'releases') $Tag
$Parent = Split-Path -Parent $Dest
New-Item -ItemType Directory -Force -Path $Parent | Out-Null
$Fresh = Join-Path $Parent ('.download-' + [guid]::NewGuid().ToString('N'))
try {
  git clone -q -c core.autocrlf=false --branch $Tag --single-branch $Source $Fresh
  if ($LASTEXITCODE -ne 0) { throw 'The release could not be downloaded.' }
  $Head = (git -C $Fresh rev-parse HEAD).Trim()
  if ($Head -ne $Commit) {
    throw "Release check failed: expected commit $Commit, the download is $Head. Nothing was installed."
  }
  if (-not (Test-Path -LiteralPath $Dest)) {
    Move-Item -LiteralPath $Fresh -Destination $Dest
  }
  # A release folder kept from an earlier run must equal the fresh download, file for file, before any of it runs.
  elseif (Compare-Object @(Get-Listing $Fresh) @(Get-Listing $Dest)) {
    throw "Release check failed: $Dest differs from a fresh download of commit $Commit. Nothing was run. Keep that folder for review, or move it away and run this again."
  }
}
finally {
  if (Test-Path -LiteralPath $Fresh) { Remove-Item -LiteralPath $Fresh -Recurse -Force }
}
Set-Location -LiteralPath $Dest
uv run --no-project --python 3.12 python -I -B setup_device.py --root $Root @args
exit $LASTEXITCODE
