param(
    [string]$MinGW = $env:MINGW64_PREFIX,
    [switch]$TestOnly,
    [string]$Sample
)
$ErrorActionPreference = 'Stop'
if (!$MinGW) { throw 'Specify -MinGW or set MINGW64_PREFIX to an AMD64 MinGW-w64 prefix.' }
$root = $PSScriptRoot
$out = Join-Path $root 'build64'
$gpp = Join-Path $MinGW 'bin\g++.exe'
if (!(Test-Path $gpp)) { throw "Compiler not found: $gpp" }
$env:PATH = "$MinGW\bin;$(Join-Path (Split-Path $MinGW -Parent) 'usr\bin');$env:PATH"
$target = & $gpp -dumpmachine
if ($LASTEXITCODE -or $target -notmatch '^x86_64-') { throw 'An AMD64 compiler is required.' }
New-Item -ItemType Directory -Force $out | Out-Null
$common = @('-std=c++17', '-O2', '-Wall', '-Wextra', '-static-libgcc', '-static-libstdc++', '-I', "$MinGW\include", '-L', "$MinGW\lib")
& $gpp @common '-DDECODER_TEST' "$root\src\jxl_decode.cpp" '-o' "$out\jxl_decode_test.exe" '-ljxl'
if ($LASTEXITCODE) { throw 'Decoder build failed.' }
$deps = @('libjxl.dll','libjxl_cms.dll','libhwy.dll','libbrotlicommon.dll','libbrotlidec.dll','libbrotlienc.dll','liblcms2-2.dll','libgcc_s_seh-1.dll','libstdc++-6.dll','libwinpthread-1.dll')
foreach ($dep in $deps) { Copy-Item "$MinGW\bin\$dep" $out -Force }
if (!$TestOnly) {
    & $gpp @common '-shared' "$root\src\jxl_decode.cpp" "$root\src\dinput_proxy64.cpp" "$root\src\dinput8_x64.def" '-o' "$out\dinput8.dll" '-ljxl' '-ladvapi32'
    if ($LASTEXITCODE) { throw 'AMD64 proxy build failed.' }
}
& $gpp @common "$root\tests\hook_regression64.cpp" "$root\src\jxl_decode.cpp" '-o' "$out\hook_regression64.exe" '-ljxl'
if ($LASTEXITCODE) { throw 'AMD64 regression build failed.' }
if ($Sample) { & "$out\hook_regression64.exe" $Sample } else { & "$out\hook_regression64.exe" }
if ($LASTEXITCODE) { throw 'AMD64 regression failed.' }
Write-Output "Built and verified AMD64 outputs in $out"
