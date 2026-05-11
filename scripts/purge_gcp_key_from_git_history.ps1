# Removes a committed GCP service-account JSON from *all* local Git history so
# GitHub push protection (GH013) will accept `git push`.
#
# BEFORE YOU RUN:
# 1. In Google Cloud Console, delete/disable this key and create a new one if needed.
# 2. Run from PowerShell inside the repo root, with `git` on PATH (where `git push` works).
# 3. Keep the file on disk for local use; .gitignore should list Tool/**/*.json and Tool/config.yaml.
#
# Usage (default path matches GitHub scanner report):
#   cd "...\Geyser Testing Department"
#   powershell -ExecutionPolicy Bypass -File .\scripts\purge_gcp_key_from_git_history.ps1
#
param(
    [string]$RelativePath = "Tool/geyser-testing-department-fbb8f1e44706.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Resolve repo root (works no matter where you invoke pwsh from, as long as you pass -File.)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
while (-not (Test-Path ".git")) {
    $parent = Split-Path -Parent (Get-Location)
    if (-not $parent -or $parent -eq (Get-Location).Path) {
        throw ".git not found — run this script from inside the repo (scripts/ folder is fine)."
    }
    Set-Location $parent
}
$root = (Get-Location).Path

Write-Host "Repo root: $root"
Write-Host "Purging path from history: $RelativePath"

# Ensure tracked files are removed from index for current tree (ignored files stay on disk).
if (Test-Path $RelativePath) {
    git rm --cached -f -- $RelativePath 2>$null
}

# Rewrite all commits on current branch; drop the secret blob from every tree.
# (If you have multiple branches with the same file, repeat on each or use -- --all with care.)
$filter = "git rm --cached --ignore-unmatch -- `"$RelativePath`""
git filter-branch --force --index-filter $filter --prune-empty HEAD

# Drop filter-branch backup refs so the old objects can be GC'd.
if (Test-Path ".git/refs/original") {
    Remove-Item -Recurse -Force ".git/refs/original"
}
git reflog expire --expire=now --all
git gc --prune=now --aggressive

Write-Host ""
Write-Host "Done. Verify with: git log -p -- $RelativePath   (should show nothing)"
Write-Host "If remote never had a good push, or only you use this repo, push with:"
Write-Host "  git push -u origin master --force"
Write-Host "If others already cloned, coordinate before force-pushing."
