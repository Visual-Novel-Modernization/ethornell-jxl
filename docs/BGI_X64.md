# AMD64 BGI support

The AMD64 DirectInput8 proxy is implemented separately in `src/dinput_proxy64.cpp`, with undecorated exports in `src/dinput8_x64.def`. The existing x86 proxy, launcher, export definition, and build script remain unchanged. The x64 proxy has been manually confirmed to start the tested game after the trampoline fix; long-term stability and other engine builds are not established.

## Build and test

Use an AMD64 MinGW-w64 toolchain with matching libjxl development libraries and runtime dependencies:

```powershell
pwsh -File ./build64.ps1 -MinGW C:\msys64\ucrt64
pwsh -File ./build64.ps1 -MinGW C:\msys64\ucrt64 -Sample C:\samples\image.cbgjxl
```

`MINGW64_PREFIX` can supply the toolchain prefix. `-TestOnly` builds the decoder and regression executable without rebuilding the proxy. Outputs are written to the ignored `build64` directory. The build verifies the compiler target and runs the machine-code regression. No game is launched.

The regression executes the same entry and return jump writers as the proxy. A fixture containing the exact stolen prologue must return `0x3420` through both the original path and the hooked standard-CBG fallback. It also reproduces the old RAX-clobbering return jump. An optional envelope sample checks the JXL path and trailing output guard bytes.

## Hook contract

The tested decoder uses the Microsoft x64 ABI: RCX is the output buffer, RDX is the CBG input, and EAX is the return code. The known decoder RVA is `0xC00C0`; installation checks the 12-byte sequence `48 89 74 24 20 41 56 B8 20 34 00 00`. The implementation also has a signature fallback, so this is not a universal decoder locator for arbitrary games.

The entry patch is 12 bytes (`mov rax, imm64; jmp rax`). The trampoline copies three complete instructions totaling 12 bytes and uses a 14-byte RIP-indirect return jump in separately allocated memory. The return jump preserves every register, including EAX, which the stolen prologue sets to `0x3420`. Using the entry jump for the return destroys that value and breaks standard-CBG decoding.

Hook setup is deferred until `DirectInput8Create`. Envelope probing checks committed readable memory regions and parses integer fields with `memcpy`. This does not establish an allocation's logical length; callers must still provide valid input buffers. Standard CBG images use the original decoder trampoline.

## Deployment

Copy the proxy and its matching runtime DLLs from `build64` alongside the target AMD64 executable in a separate game copy. Preserve all original root files and directories, including root-level system archives, not only the `Archive` directory. Keep the original game intact.

The working candidate used only validated 24bpp replacements and retained original 32bpp images. The decoder contains a 32bpp path, but this run did not establish that converting every 32bpp asset is safe. In particular, BGRX data must not be treated as BGRA without an established alpha contract.

For batch conversion, select the architecture explicitly:

```powershell
pwsh -File ./tools/batch_jxl.ps1 -GameDir C:\work -DecoderPath ./build64/jxl_decode_test.exe -GARbroPath C:\tools\GARbro -JxlBinDir C:\tools\jxl
```

The batch tool handles WPF exposing 24bpp sources as `Bgr32` by converting them to `Bgr24`; 32bpp sources must be `Bgra32`. Independent/native decoder agreement is not a measurement of lossy error against the original image. Evaluate source fidelity separately before deployment.

Start the game manually and test both ordinary CBG and converted resources. For an original-game comparison, restore the original archives as well as removing the proxy; removing only the proxy cannot decode JXL envelopes.
