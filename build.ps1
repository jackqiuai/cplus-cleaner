param([switch]$SkipTests)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$cargoExecutable=Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
if (!(Test-Path -LiteralPath $cargoExecutable)) { throw 'Install Rust with the x86_64-pc-windows-msvc toolchain first.' }
if (!$SkipTests) { & $cargoExecutable test --workspace --locked; if ($LASTEXITCODE) { throw 'Regression tests failed.' } }
& $cargoExecutable build --release --locked -p cplus-cleaner
if ($LASTEXITCODE) { throw 'Release build failed.' }
& (Join-Path $PSScriptRoot 'package.ps1') -Executable (Join-Path $PSScriptRoot 'target\release\CPlusCleaner.exe')
