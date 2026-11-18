# Installer round trip in a throwaway HOME (Windows). Spends no credits:
# the first run skips the key check, the second uses a key the API rejects.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$FakeHome = Join-Path ([IO.Path]::GetTempPath()) ('scavio-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $FakeHome | Out-Null
function Fail($msg) { Write-Error "FAIL: $msg"; exit 1 }
try {
    $env:SCAVIO_INSTALL_HOME = $FakeHome
    $env:CLAUDE_SKILLS_DIR = $null
    $Key = 'sk_test_install_roundtrip_0123456789'
    $env:SCAVIO_API_KEY = $Key
    $out = & (Join-Path $Root 'install.ps1') -NoCheck *>&1 | Out-String
    if ($out -match $Key) { Fail 'installer printed the key' }
    $Skill = Join-Path $FakeHome '.claude\skills\seo-scavio'
    $Config = Join-Path $FakeHome '.config\claude-seo\scavio.json'
    if (-not (Test-Path (Join-Path $Skill 'SKILL.md'))) { Fail 'SKILL.md not installed' }
    if (-not (Test-Path (Join-Path $Skill 'scavio_seo.py'))) { Fail 'scavio_seo.py not installed' }
    if (-not (Test-Path $Config)) { Fail 'config not written' }
    if ((Get-Content $Config -Raw | ConvertFrom-Json).api_key -ne $Key) { Fail 'config does not hold the key' }
    $acl = Get-Acl $Config
    if (-not $acl.AreAccessRulesProtected) { Fail 'config ACL still inherits' }
    if (@($acl.Access).Count -ne 1) { Fail "config ACL has $(@($acl.Access).Count) rules, want 1" }

    $other = Join-Path $FakeHome '.config\claude-seo\other.json'
    Set-Content -Path $other -Value '{}'
    & (Join-Path $Root 'uninstall.ps1') | Out-Null
    if (Test-Path $Skill) { Fail 'skill not removed' }
    if (Test-Path $Config) { Fail 'config not removed' }
    if (-not (Test-Path $other)) { Fail 'uninstall removed another integration file' }

    $env:SCAVIO_API_KEY = 'sk_invalid_key_for_install_test'
    $failed = $false
    try { & (Join-Path $Root 'install.ps1') *>&1 | Out-Null } catch { $failed = $true }
    if (-not $failed -and $LASTEXITCODE -eq 0) { Fail 'installer accepted an invalid key' }
    if (Test-Path $Config) { Fail 'config written after a rejected key' }
    if (Test-Path $Skill) { Fail 'skill installed after a rejected key' }
    Write-Host 'install round trip (Windows): OK'
    # The rejected-key run above leaves $LASTEXITCODE at 1 on purpose.
    $global:LASTEXITCODE = 0
}
finally {
    $env:SCAVIO_INSTALL_HOME = $null
    $env:SCAVIO_API_KEY = $null
    Remove-Item -Recurse -Force $FakeHome -ErrorAction SilentlyContinue
}
exit 0
