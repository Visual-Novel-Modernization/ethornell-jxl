param(
    [switch]$TestOnly,
    [string]$MinGW = $env:MINGW32_PREFIX
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (!$MinGW) {
    $drive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
    $candidates = @(
        $env:MINGW32_HOME,
        "$drive\msys64\mingw32",
        "$drive\mingw32"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path (Join-Path $c 'bin\g++.exe'))) { $MinGW = $c; break }
    }
}
if (!$MinGW -or !(Test-Path (Join-Path $MinGW 'bin\g++.exe'))) {
    $gpp = Get-Command 'g++.exe' -ErrorAction SilentlyContinue
    if ($gpp) {
        $binDir = Split-Path $gpp.Source -Parent
        if (Test-Path (Join-Path $binDir 'g++.exe')) {
            $MinGW = Split-Path $binDir -Parent
        }
    }
}
if (!$MinGW -or !(Test-Path (Join-Path $MinGW 'bin\g++.exe'))) {
    throw "MinGW32 i686 toolchain not found. Specify -MinGW or set MINGW32_PREFIX."
}
$env:PATH = "$MinGW\bin;$env:PATH"
$out = Join-Path $root 'build'; New-Item -ItemType Directory -Force $out | Out-Null

& "$MinGW\bin\g++.exe" -std=c++17 -O2 -Wall -Wextra -DDECODER_TEST (Join-Path $root 'src\jxl_decode.cpp') -o (Join-Path $out 'jxl_decode_test.exe') -ljxl
if ($LASTEXITCODE) { throw 'decoder build failed' }

$deps = @('libjxl.dll','libjxl_cms.dll','libhwy.dll','libbrotlicommon.dll','libbrotlidec.dll','libbrotlienc.dll','liblcms2-2.dll','libgcc_s_dw2-1.dll','libstdc++-6.dll','libwinpthread-1.dll')
foreach ($dep in $deps) { Copy-Item -LiteralPath (Join-Path "$MinGW\bin" $dep) -Destination $out -Force }

if (!$TestOnly) {
    & "$MinGW\bin\g++.exe" -std=c++17 -O2 -Wall -Wextra -shared -static-libgcc -static-libstdc++ (Join-Path $root 'src\jxl_decode.cpp') (Join-Path $root 'src\dinput_proxy.cpp') (Join-Path $root 'src\dinput8.def') -o (Join-Path $out 'dinput8.dll') -ljxl -ladvapi32
    if ($LASTEXITCODE) { throw 'dinput8 build failed' }
}

& "$MinGW\bin\g++.exe" -std=c++17 -O2 (Join-Path $root 'tests\hook_test.cpp') -o (Join-Path $out 'hook_test32.exe')
if ($LASTEXITCODE) { throw 'hook test build failed' }

Write-Output 'Built core x86 JXL decoder, DirectInput proxy and regression tests.'
