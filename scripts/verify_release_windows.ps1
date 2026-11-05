# Run only in a disposable GitHub-hosted Windows runner. Tests the public ZIP,
# not the maintainer's local installation or a prebuilt virtual environment.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows') {
    throw 'This verification requires a disposable GitHub Actions Windows runner.'
}
if ($env:RELEASE_TAG -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') { throw 'Invalid release tag' }
$repoRoot = Split-Path $PSScriptRoot -Parent
$downloadRoot = Join-Path $env:RUNNER_TEMP 'cw-public-release'
New-Item -ItemType Directory -Path $downloadRoot | Out-Null
$name = "content-workbench-community-$($env:RELEASE_TAG).zip"
$releaseUrl = "https://github.com/$($env:GITHUB_REPOSITORY)/releases/download/$($env:RELEASE_TAG)"
$zipPath = Join-Path $downloadRoot $name
Invoke-WebRequest "$releaseUrl/$name" -OutFile $zipPath
Invoke-WebRequest "$releaseUrl/SHA256SUMS.txt" -OutFile (Join-Path $downloadRoot 'SHA256SUMS.txt')
$sumLine = Get-Content (Join-Path $downloadRoot 'SHA256SUMS.txt') | Where-Object { $_.EndsWith("  $name") }
if (@($sumLine).Count -ne 1) { throw 'Expected exactly one release checksum' }
$expected = ($sumLine -split '\s+')[0]
if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash -ne $expected) { throw 'Release checksum mismatch' }
Expand-Archive -LiteralPath $zipPath -DestinationPath $downloadRoot
$packageRoot = Join-Path $downloadRoot "content-workbench-community-$($env:RELEASE_TAG)"
foreach ($path in @('.venv','web/node_modules','web/dist','settings.json','secrets.json')) {
    if (Test-Path (Join-Path $packageRoot $path)) { throw "Unexpected bundled artifact: $path" }
}
$env:CW_DATA_DIR = Join-Path $env:RUNNER_TEMP 'cw-private-test-data'
& (Join-Path $packageRoot 'deploy/install-windows.ps1') -NoBrowser
if ($LASTEXITCODE -ne 0) { throw 'Installation failed' }
$state = Invoke-RestMethod 'http://localhost:8765/api/auth/state'
if ($state.login_mode -ne 'local') { throw 'Wrong server login mode' }
$drafts = Invoke-RestMethod 'http://localhost:8765/api/drafts'
if (@($drafts).Count -ne 0) { throw 'Expected empty user database' }
$settings = Get-Content (Join-Path $env:CW_DATA_DIR 'settings.json') -Raw | ConvertFrom-Json
if ($settings.llm_backend -ne 'disabled' -or $settings.feishu_auto_sync -or $settings.feishu_auto_archive) {
    throw 'Unexpected external automation defaults'
}
$python = Join-Path $packageRoot '.venv/Scripts/python.exe'
# A maintained harness tests the released installed package in another temporary
# data directory, including real process restart and attachment persistence.
& $python (Join-Path $repoRoot 'scripts/smoke_local.py')
if ($LASTEXITCODE -ne 0) { throw 'HTTP persistence and restart verification failed' }
Push-Location $packageRoot
try {
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Test browser installation failed' }
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Released package regression tests failed' }
    @'
from playwright.sync_api import sync_playwright, expect
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://localhost:8765/#/drafts')
    expect(page.get_by_role('heading', name='稿件', exact=True)).to_be_visible(timeout=20000)
    expect(page.get_by_role('combobox', name='新建平台稿')).to_be_visible()
    assert not errors, errors
    browser.close()
print('PASS: real Chromium rendered the released workbench and draft controls without page errors.')
'@ | & $python -X utf8 -
    if ($LASTEXITCODE -ne 0) { throw 'Browser verification failed' }
} finally { Pop-Location }
@"
## Clean Windows installation: PASS

- Release: $($env:RELEASE_TAG)
- Public ZIP SHA256: $expected
- New Python environment; dependencies and frontend built from lock files
- Empty private database; external automation disabled
- Real draft/image save, readback and process restart: passed
- Released regression suite: passed
- Real Chromium workbench page and controls: passed

This verifies this release on a fresh GitHub Windows VM. It does not test WorkBuddy itself, paid models, external account authorization, every Windows version, or the recipient's network.
"@ | Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding utf8
