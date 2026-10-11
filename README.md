# BGI / Ethornell JXL Hook & Launcher

[Japanese Documentation](README.ja.md)

A universal local JPEG XL (JXL) image decoding compatibility layer for 32-bit visual novel games running on the BGI / Ethornell engine.

It seamlessly enables JXL image compression in BGI game archives by intercepting the engine's internal CBG decompression routine in memory and transparently decoding JXL-encoded payloads while preserving 100% backward compatibility for untouched assets.

---

## Features

- **In-Memory CBG Interception**: Hooks the classic BGI CBG decompression function via a prologue signature scan (`55 8B EC B8 FC 33 00 00`), making it version-agnostic and resilient across different game builds.
- **Transparent JXL Decoding**: Directly decodes JXL payloads tagged with `JXLCBG01` into engine-native BGRX pixel buffers using `libjxl`. Standard CBG images pass through to the original engine decoder without overhead.
- **Dual Deployment Options**:
  - **DirectInput8 Proxy (`dinput8.dll`)**: Standard drop-in DLL hijacking for native executables. No extra launcher required.
  - **Universal Launcher (`bgi_launcher.exe`)**: Synchronized remote injector for executables packaged with application virtualization (e.g., Enigma Virtual Box) that block system DLL search hijacking.

---

## Why JPEG XL? (Compression Benchmark & Performance)

Visual novel archives commonly suffer from immense disk footprints due to legacy compression algorithms (such as custom LZSS/RLE in classic CBG). Transitioning to JPEG XL achieves **over 90% storage savings on image assets** while preserving pristine visual quality via Butteraugli psycho-visual modeling.

### Empirical Benchmark on Real-World BGI Game Assets (1,796 Images)

The following metrics are derived from verified full-game conversion runs (`quality=90`, `effort=8`):

| Evaluation Metric | Classic BGI CBG (Original) | JPEG XL Layer (This Project) | Improvement / Efficiency |
| :--- | :--- | :--- | :--- |
| **Target Image Payload** | **4,095.1 MB** (~4.10 GB) | **393.8 MB** (~0.39 GB) | **-90.24% reduction (10.4x smaller)** |
| **Total Game Size (incl. Audio/Movies)** | **9,634.9 MB** (~9.63 GB) | **5,933.6 MB** (~5.93 GB) | **-38.42% overall (-3.70 GB saved)** |
| **Max Channel Error (0-255)** | 0 (Bit-exact) | **1** | Perceptually indistinguishable |
| **Root Mean Square Error (Max RMSE)** | 0.0000 | **0.5668** | Mathematically sub-pixel accuracy |
| **Visual Fidelity (Psycho-visual)** | Baseline | **Visually Lossless** | Zero visible ringing / banding |
| **SIMD Hardware Acceleration** | Scalar x86 fallback | **AVX2 / AVX-512 via Highway** | Near-instantaneous decode |

### Modern Image Format Comparison in Visual Novel Engines

| Attribute / Feature | Classic CBG | WebP | AVIF | JPEG XL (JXL) |
| :--- | :--- | :--- | :--- | :--- |
| **Compression Ratio vs CBG** | Baseline (0%) | ~50% - 65% | ~75% - 82% | **> 90% (Outstanding)** |
| **High-Frequency Detail Preservation** | Lossless (Bloated) | Noticeable blurring / smudging | Chroma subsampling blur | **Razor-sharp anime line art & gradients** |
| **Color Banding / Compression Artifacts** | None | Visible color banding in dark areas | Blocking at low bitrates | **Virtually artifact-free (Butteraugli metric)** |
| **CPU Decode Efficiency on x86** | Single-threaded LZSS | Standard libwebp | Heavy CPU overhead | **Hardware SIMD accelerated via Google Highway** |
| **Direct-to-BGRX Engine Feeding** | Requires conversion | Requires conversion | Requires conversion | **Direct engine ABI buffer delivery** |

> **Key Takeaway**: By compressing 1,796 high-resolution assets, game archives drop from **4.1 GB to under 400 MB**, freeing up **3.7 GB of disk space** with mathematically verified visual fidelity (Max pixel deviation <= 1, RMSE < 0.57).

---

## Repository Structure

```
.
|-- src/
|   |-- dinput_proxy.cpp   # Dual-mode DirectInput8 proxy and injectable hook DLL
|   |-- launcher.cpp       # Universal launcher with sequential target resolution
|   |-- jxl_decode.cpp     # libjxl-based CBG decoding implementation
|   \-- dinput8.def        # DirectInput8 export definition table
|-- tests/
|   \-- hook_test.cpp      # Standalone 32-bit trampoline & fallback validation suite
|-- tools/
|   |-- arc20.py           # Universal BURIKO ARC20 archive indexer
|   |-- batch_jxl.ps1      # Multi-worker batch JXL compression pipeline
|   |-- repack_batch.py    # Zero-loss ARC20 stream repacker
|   \-- replace_arc20.py   # Single-asset ARC20 payload replacement tool
|-- docs/                  # Technical notes and compression reports
|-- build.ps1              # Automated PowerShell build script
\-- LICENSE                # MIT License
```

---

## Deployment & Launcher

### Mode 1: Native Game Executable (Proxy Mode)
For unmodified native games:
1. Copy `dinput8.dll` and the runtime libraries (`libjxl.dll`, `libhwy.dll`, `libbrotli*.dll`, etc.) from `build/` into the game root directory (alongside `bgi.exe`).
2. Start the game normally.

### Mode 2: Virtualized Executables (Launcher Mode)
For games packaged using Enigma Virtual Box or similar runtime virtualizers:
1. Copy `bgi_launcher.exe`, `bgi_jxl_hook.dll`, and runtime libraries into the game folder.
2. Launch via `bgi_launcher.exe`.

#### Target Executable Resolution
`bgi_launcher.exe` sequentially resolves the game executable using the following deterministic strategy:
1. **Command-Line Argument**: Explicit target path passed via `bgi_launcher.exe [path_to_game.exe]`.
2. **Configuration File**: Target specified in `launcher.ini`:
   ```ini
   [Launcher]
   Target=game.exe
   ; Optional: explicitly specify companion DLL to await before injecting
   MilestoneDll=companion.dll
   ```
3. **Sequential Auto-Discovery**:
   - Scans all `.exe` files in the directory.
   - Filters out non-game utilities (matching keywords: `launcher`, `hook`, `setup`, `install`, `unins`, `config`, `cfg`, `update`, `test`, `tool`, `dump`, `arc20`, `repack`).
   - Prioritizes standard engine executables (`bgi.exe` or names containing `bgi` / `ethornell`).
   - Falls back to sorting remaining candidates by file size in descending order (main game executables are significantly larger than helper tools).

#### Virtualization Synchronization
When an `.enigma` section is detected in the target binary:
1. The launcher starts the process normally and monitors loaded modules.
2. Once the companion module or target executable base finishes unpacking, the launcher immediately suspends the primary thread (`SuspendThread`).
3. Injects `bgi_jxl_hook.dll` via `CreateRemoteThread` + `LoadLibraryW`.
4. Resumes the primary thread (`ResumeThread`) before any image assets are read by the game.

---

## Building

Prerequisites:
- 32-bit MinGW-w64 toolchain (i686).
- 32-bit `libjxl` development libraries.

```powershell
# Default build (Proxy mode only: builds dinput8.dll, runtime deps, and tests)
pwsh -File ./build.ps1

# Full build (Includes bgi_launcher.exe and bgi_jxl_hook.dll)
pwsh -File ./build.ps1 -WithLauncher

# Fast build (Launcher only)
pwsh -File ./build.ps1 -LauncherOnly

# Test suite only
pwsh -File ./build.ps1 -TestOnly
```

To run the standalone test suite:
```powershell
./build/hook_test32.exe ./build/dinput8.dll <path_to_sample.cbgjxl>
```

---

## Asset Encoding Specification

- **Target Assets**: 24-bit CBG type 1 (opaque BGR24), header flag at offset 22 is `0`.
- **Envelope**: Preserves the 16-byte BGI image header, prefixed with `CompressedBG___` and marked with `JXLCBG01`.
- **Output**: 16-byte header + BGRX pixel data (depth 32, format 7).
- Standard CBG assets continue through the original engine trampoline without degradation.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

Copyright (c) 2026 [Visual Novel Modernization](https://github.com/Visual-Novel-Modernization)

