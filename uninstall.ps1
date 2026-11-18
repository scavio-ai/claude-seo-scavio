# Scavio for Claude SEO: uninstaller (Windows PowerShell 5.1+ and PowerShell 7).
# Removes the seo-scavio skill and the stored key. Touches nothing else.
$ErrorActionPreference = 'Stop'

# SCAVIO_INSTALL_HOME replaces the home folder; tests use it.
$UserHome = if ($env:SCAVIO_INSTALL_HOME) { $env:SCAVIO_INSTALL_HOME } else { $HOME }
$SkillsDir = if ($env:CLAUDE_SKILLS_DIR) { $env:CLAUDE_SKILLS_DIR } else { Join-Path $UserHome '.claude\skills' }
$Target = Join-Path $SkillsDir 'seo-scavio'
$ConfigDir = Join-Path $UserHome '.config\claude-seo'
$Config = Join-Path $ConfigDir 'scavio.json'

if (Test-Path $Target) { Remove-Item -Recurse -Force $Target; Write-Host "Removed $Target" }
else { Write-Host "No skill at $Target" }

if (Test-Path $Config) { Remove-Item -Force $Config; Write-Host "Removed $Config" }

# Other Claude SEO integrations may keep files here; remove the folder only if empty.
if ((Test-Path $ConfigDir) -and -not (Get-ChildItem -Force $ConfigDir)) { Remove-Item -Force $ConfigDir }

Write-Host 'Done.'
