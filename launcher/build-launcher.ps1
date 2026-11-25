<#
Build locally from any working directory (no downloads, installs, signing, or execution):
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\launcher\build-launcher.ps1
The execution-policy override applies only to this build process, not machine policy.

Requires the existing Windows .NET Framework v4 compiler and assets\tray_icon.ico.
The script resolves source/resources/output relative to itself, not the caller's CWD.
It emits the UNSIGNED root NewsDigest.exe; distribute source/build script, not this EXE.
The compiler's default Win32 manifest requests asInvoker (no administrator elevation).
Do not use /nowin32manifest: that would remove the explicit non-elevating manifest.
Rebuild procedure is repeatable; legacy csc embeds a PE timestamp, so byte-identical
hashes are not promised. No shortcuts, startup entries, or application data are changed.
An existing EXE is replaced only if its FileDescription AND CompanyName match ours;
metadata is an accidental-overwrite guard, not an authenticity or signature check.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$source = Join-Path $PSScriptRoot 'NewsDigestLauncher.cs'
$icon = Join-Path $root 'assets\tray_icon.ico'
$output = Join-Path $root 'NewsDigest.exe'

# Resolve the native Windows directory, never PATH or a downloaded compiler.
$windowsRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows)
$frameworks = @(
    (Join-Path $windowsRoot 'Microsoft.NET\Framework64\v4.0.30319'),
    (Join-Path $windowsRoot 'Microsoft.NET\Framework\v4.0.30319')
)
$framework = $null
foreach ($candidate in $frameworks) {
    if (Test-Path -LiteralPath (Join-Path $candidate 'csc.exe') -PathType Leaf) {
        $framework = $candidate
        break
    }
}
if ($null -eq $framework) {
    throw 'The existing Windows .NET Framework v4 C# compiler was not found. Nothing was installed.'
}
$csc = Join-Path $framework 'csc.exe'
$system = Join-Path $framework 'System.dll'
$forms = Join-Path $framework 'System.Windows.Forms.dll'
foreach ($inputFile in @($source, $icon, $system, $forms)) {
    if (-not (Test-Path -LiteralPath $inputFile -PathType Leaf)) {
        throw "Required build input is missing: $inputFile"
    }
}

# Check immediately before invoking the compiler; refuse unknown pre-existing outputs.
if (Test-Path -LiteralPath $output) {
    if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
        throw "Refusing to overwrite a non-file output: $output"
    }
    $existing = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($output)
    if ($existing.FileDescription -cne 'News Digest' -or $existing.CompanyName -cne 'Wyakh') {
        throw "Refusing to overwrite an unrecognized executable: $output"
    }
}

# Each option is a separate argument; quote embedded paths for legacy csc on Windows.
$compilerArguments = @(
    '/nologo',
    '/noconfig',
    '/target:winexe',
    '/platform:anycpu',
    '/optimize+',
    '/debug-',
    '/warnaserror+',
    ('/reference:"{0}"' -f $system),
    ('/reference:"{0}"' -f $forms),
    ('/win32icon:"{0}"' -f $icon),
    ('/out:"{0}"' -f $output),
    ('"{0}"' -f $source)
)
& $csc @compilerArguments
if ($LASTEXITCODE -ne 0) {
    throw "C# compiler failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
    throw "Compiler reported success but output is missing: $output"
}

Write-Output "Built UNSIGNED launcher (not executed): $output"
Get-FileHash -LiteralPath $output -Algorithm SHA256
