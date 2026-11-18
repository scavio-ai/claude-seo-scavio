# Scavio for Claude SEO: installer (Windows PowerShell 5.1+ and PowerShell 7).
#
# Copies the seo-scavio skill to ~\.claude\skills\seo-scavio\ and stores your
# Scavio API key in ~\.config\claude-seo\scavio.json, readable only by you.
# It checks the key with a free call before writing anything. Claude SEO's own
# files are not changed, and nothing is installed with pip or npm.
#
# The key comes from $env:SCAVIO_API_KEY when it is set, otherwise from a
# hidden prompt. Pass -NoCheck to skip the key check (for offline installs).
[CmdletBinding()]
param([switch]$NoCheck)

$ErrorActionPreference = 'Stop'

# SCAVIO_INSTALL_HOME replaces the home folder; tests use it.
$UserHome = if ($env:SCAVIO_INSTALL_HOME) { $env:SCAVIO_INSTALL_HOME } else { $HOME }
$SkillsDir = if ($env:CLAUDE_SKILLS_DIR) { $env:CLAUDE_SKILLS_DIR } else { Join-Path $UserHome '.claude\skills' }
$Target = Join-Path $SkillsDir 'seo-scavio'
$ConfigDir = Join-Path $UserHome '.config\claude-seo'
$Config = Join-Path $ConfigDir 'scavio.json'
$Source = Join-Path $PSScriptRoot 'skills\seo-scavio'

Write-Host 'Scavio for Claude SEO: installer'

$Python = $null
foreach ($cand in @('python3', 'python', 'py')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        & $cmd.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = $cmd.Source; break }
    }
}
if (-not $Python) { Write-Error 'Python 3.9 or later is required.'; exit 1 }

$Key = $env:SCAVIO_API_KEY
if (-not $Key) {
    Write-Host 'Get a key at https://dashboard.scavio.dev/sign-up (new accounts get 50 free credits, no card).'
    $secure = Read-Host -AsSecureString 'Scavio API key'
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $Key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if (-not $Key) { Write-Error 'No key given. Nothing was changed.'; exit 1 }

$prevKey = $env:SCAVIO_API_KEY
try {
    # The key travels in the environment, not on the command line.
    $env:SCAVIO_API_KEY = $Key
    if (-not $NoCheck) {
        & $Python (Join-Path $Source 'scavio_seo.py') check | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Error 'The key check failed (see the line above). Nothing was changed.'; exit 1 }
        Write-Host 'Key accepted.'
    }

    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $tmp = Join-Path $ConfigDir ('.scavio.' + [guid]::NewGuid().ToString('N') + '.json')
    # Create the file empty, restrict it to the current user, then write the key.
    New-Item -ItemType File -Path $tmp -Force | Out-Null
    $onWindows = ($PSVersionTable.PSVersion.Major -lt 6) -or $IsWindows
    if ($onWindows) {
        $acl = Get-Acl $tmp
        $acl.SetAccessRuleProtection($true, $false)
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($me, 'FullControl', 'Allow')
        $acl.SetAccessRule($rule)
        Set-Acl -Path $tmp -AclObject $acl
    } else {
        chmod 600 $tmp
    }
    $json = @{ api_key = $Key } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding($false)))
    Move-Item -Force -Path $tmp -Destination $Config
    Write-Host "Saved the key to $Config (readable only by you)."
}
finally {
    $env:SCAVIO_API_KEY = $prevKey
    $Key = $null
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -Force (Join-Path $Source 'SKILL.md') (Join-Path $Target 'SKILL.md')
Copy-Item -Force (Join-Path $Source 'scavio_seo.py') (Join-Path $Target 'scavio_seo.py')
Write-Host "Installed the skill to $Target."
Write-Host ''
Write-Host 'Done. Open a new Claude Code session and try:'
Write-Host '  /seo-scavio visibility "best crm for small agencies" --brand YourBrand --domain yourbrand.com'
