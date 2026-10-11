param(
    [switch]$TestOnly,
    [switch]$WithLauncher,
    [switch]$LauncherOnly,
    [string]$MinGW = $env:MINGW32_PREFIX
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (!$MinGW) {
    $drive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
    $candidates = @(
        $env:MINGW32_HOME,
        "$drive\msys64\mingw32",
        "$drive\mingw32",
        (Get-ChildItem -Path "$drive\*\msys64\mingw32" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName -First 1),
        (Get-ChildItem -Path "$drive\*\mingw32" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName -First 1)
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

# 1. Launcher-only fast build
if ($LauncherOnly) {
    & "$mingw\bin\g++.exe" -std=c++17 -O2 -Wall -Wextra -static -static-libgcc -static-libstdc++ -mwindows -municode (Join-Path $root 'src\launcher.cpp') -o (Join-Path $out 'bgi_launcher.exe') -lshell32
    if ($LASTEXITCODE) { throw 'launcher build failed' }
    Write-Output 'Built bgi_launcher.exe successfully.'
    return
}

# 2. Decoder test and dependencies
& "$mingw\bin\g++.exe" -std=c++17 -O2 -Wall -Wextra -DDECODER_TEST (Join-Path $root 'src\jxl_decode.cpp') -o (Join-Path $out 'jxl_decode_test.exe') -ljxl
if ($LASTEXITCODE) { throw 'decoder build failed' }

$deps = @('libjxl.dll','libjxl_cms.dll','libhwy.dll','libbrotlicommon.dll','libbrotlidec.dll','libbrotlienc.dll','liblcms2-2.dll','libgcc_s_dw2-1.dll','libstdc++-6.dll','libwinpthread-1.dll')
foreach ($dep in $deps) { Copy-Item -LiteralPath (Join-Path "$mingw\bin" $dep) -Destination $out -Force }

# 3. Core Hook DLL (DirectInput8 proxy for native release)
if (!$TestOnly) {
    & "$mingw\bin\g++.exe" -std=c++17 -O2 -Wall -Wextra -shared -static-libgcc -static-libstdc++ (Join-Path $root 'src\jxl_decode.cpp') (Join-Path $root 'src\dinput_proxy.cpp') (Join-Path $root 'src\dinput8.def') -o (Join-Path $out 'dinput8.dll') -ljxl -ladvapi32
    if ($LASTEXITCODE) { throw 'dinput8 build failed' }

    # 4. Optional Launcher and dedicated injectable hook DLL
    if ($WithLauncher) {
        & "$mingw\bin\g++.exe" -std=c++17 -O2 -Wall -Wextra -shared -static-libgcc -static-libstdc++ (Join-Path $root 'src\jxl_decode.cpp') (Join-Path $root 'src\dinput_proxy.cpp') -o (Join-Path $out 'bgi_jxl_hook.dll') -ljxl -ladvapi32
        if ($LASTEXITCODE) { throw 'bgi_jxl_hook build failed' }

        & "$mingw\bin\g++.exe" -std=c++17 -O2 -Wall -Wextra -static -static-libgcc -static-libstdc++ -mwindows -municode (Join-Path $root 'src\launcher.cpp') -o (Join-Path $out 'bgi_launcher.exe') -lshell32
        if ($LASTEXITCODE) { throw 'launcher build failed' }
    }
}

# 5. Hook test suite
& "$mingw\bin\g++.exe" -std=c++17 -O2 (Join-Path $root 'tests\hook_test.cpp') -o (Join-Path $out 'hook_test32.exe')
if ($LASTEXITCODE) { throw 'hook test build failed' }

if ($WithLauncher) {
    Write-Output 'Built core dinput8.dll proxy, standalone bgi_jxl_hook.dll, bgi_launcher.exe and tests.'
} else {
    Write-Output 'Built core dinput8.dll proxy, runtime dependencies and tests (Launcher skipped, use -WithLauncher to build).'
}

