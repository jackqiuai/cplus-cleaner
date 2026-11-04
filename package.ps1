param([Parameter(Mandatory=$true)][string]$Executable,[switch]$RequireSigned)
$ErrorActionPreference='Stop'
$source=(Resolve-Path -LiteralPath $Executable).Path
if ($RequireSigned) {
  $signature=Get-AuthenticodeSignature -LiteralPath $source
  if ($signature.Status -ne 'Valid' -or !$signature.SignerCertificate) { throw 'A trusted Authenticode signature is required.' }
}
$destination=Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$binary=Join-Path $destination 'CPlusCleaner.exe'
if ($source -ne $binary) { Copy-Item -LiteralPath $source -Destination $binary -Force }
$report=Join-Path $destination 'build-validation.json'
$process=Start-Process -FilePath $binary -ArgumentList ('--smoke-test "'+$report+'"') -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -or !(Test-Path -LiteralPath $report) -or !((Get-Content -LiteralPath $report -Raw | ConvertFrom-Json).ok)) { throw 'Executable bundle validation failed.' }
$package=Join-Path $destination 'CPlusCleaner-windows-x64.zip'
$files=@($binary,(Join-Path $PSScriptRoot 'LICENSE'),(Join-Path $PSScriptRoot 'README.md'),(Join-Path $PSScriptRoot 'README.zh-CN.md'),(Join-Path $PSScriptRoot 'README.zh-TW.md'),(Join-Path $PSScriptRoot 'docs'))
Compress-Archive -LiteralPath $files -DestinationPath $package -Force
$hashes=@($binary,$package) | ForEach-Object { $hash=Get-FileHash -Algorithm SHA256 -LiteralPath $_; $hash.Hash.ToLowerInvariant()+'  '+[IO.Path]::GetFileName($_) }
[IO.File]::WriteAllLines((Join-Path $destination 'SHA256SUMS.txt'),$hashes)
Write-Host ('Ready: '+$binary)
