param(
  [string]$Destination = "$env:LOCALAPPDATA\TicketBunker"
)
$ErrorActionPreference = "Stop"
$Bundle = Split-Path -Parent $MyInvocation.MyCommand.Path
$Manifest = Get-Content (Join-Path $Bundle "manifest.json") -Raw | ConvertFrom-Json
$Install = Join-Path (Join-Path $Destination "releases") $Manifest.ticketbunker_version
if (Test-Path $Install) { throw "TicketBunker $($Manifest.ticketbunker_version) is already installed at $Install" }
New-Item -ItemType Directory -Force (Split-Path -Parent $Install) | Out-Null
Copy-Item $Bundle $Install -Recurse
$Bin = Join-Path $Destination "bin"
New-Item -ItemType Directory -Force $Bin | Out-Null
$Shim = Join-Path $Bin "tb.cmd"
$Target = Join-Path $Install "bin\tb.exe"
Set-Content -Path $Shim -Encoding ASCII -Value "@`"$Target`" %*"
Write-Host "Installed TicketBunker $($Manifest.ticketbunker_version) at $Install"
Write-Host "Add $Bin to PATH, then run: tb init"
