$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $root "build"
$target = Join-Path $buildDir "AutoAnime-clean"
$frontendDir = Join-Path $root "frontend"
$versionFile = Join-Path $frontendDir "src\version.js"

function Invoke-Checked {
    param(
        [string] $FilePath,
        [string[]] $Arguments,
        [string] $WorkingDirectory
    )

    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$FilePath failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $frontendDir)) {
    throw "frontend directory not found"
}

$npm = "npm.cmd"
$viteCmd = Join-Path $frontendDir "node_modules\.bin\vite.cmd"
if (-not (Test-Path -LiteralPath $viteCmd)) {
    Write-Host "Frontend dependencies missing, running npm ci..."
    Invoke-Checked -FilePath $npm -Arguments @("ci") -WorkingDirectory $frontendDir
}

$originalVersion = $null
if (Test-Path -LiteralPath $versionFile) {
    $originalVersion = Get-Content -LiteralPath $versionFile -Raw
}

try {
    Invoke-Checked -FilePath $npm -Arguments @("run", "build") -WorkingDirectory $frontendDir
}
finally {
    if ($null -ne $originalVersion) {
        [System.IO.File]::WriteAllText($versionFile, $originalVersion, [System.Text.UTF8Encoding]::new($false))
    }
}

if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $target | Out-Null

robocopy $root $target /MIR `
    /XD ".git" "build" "data" "test-data" "node_modules" ".vite" "__pycache__" "_references" "logs" ".tmp-smoke" ".tmp-smoke-download" ".tmp-smoke-download2" ".tmp-smoke-media" ".tmp-smoke-media2" `
    /XF "*.zip" "*.log" "*.pyc" ".env" `
    /R:2 /W:2 /NFL /NDL /NP | Out-Null

if ($LASTEXITCODE -ge 8) {
    throw "Package failed. Robocopy exit code: $LASTEXITCODE"
}

Get-ChildItem -LiteralPath $target -Recurse -Filter "*.sh" | ForEach-Object {
    $text = [System.IO.File]::ReadAllText($_.FullName)
    $text = $text -replace "`r`n", "`n"
    [System.IO.File]::WriteAllText($_.FullName, $text, [System.Text.UTF8Encoding]::new($false))
}

Write-Host "Created:"
Write-Host $target
