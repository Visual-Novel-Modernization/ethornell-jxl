# Fast CBG conversion and ARC20 preservation tools

These are separate tools; `batch_jxl.ps1`, `arc20.py`, and `repack_batch.py` are unchanged. Use one task root per game (Cinderella FD and Cinderella2), with each game's entire original archive tree under `<task>\original\`. Commands below are examples, not executed game batches.

## Convert

Run **Windows PowerShell 5.1** (`powershell.exe`), because GARbro uses .NET Framework and WPF. The converter defaults to the supplied GARbro directory and cjxl executable, and accepts explicit overrides.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\batch_jxl_fast.ps1 -GameDir 'C:\work\CinderellaFD-task'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\batch_jxl_fast.ps1 -GameDir 'C:\work\Cinderella2-task'
```

For bounded trials use `-Limit 1`. For parallel processes use distinct `-Worker 0 -Workers 4` through `-Worker 3 -Workers 4`; all workers must use the same worker count. Each process gives cjxl two threads. Do not run duplicate instances of the same worker for the same task root.

The converter scans every ARC20 `.arc` recursively, including tachie archives, without relying on an inventory. Only the exact 16-byte magic `b'CompressedBG___\0'` identifies CBG. Supported conversion metadata is depth 24 or 32 with the uint16 layout flag at byte 22 equal to zero. Other CBG metadata intentionally retains the original. Supported sources decode once through GARbro to PNG, then encode with `-q 90 -e 7 --num_threads=2`. There is no djxl invocation, decoded-pixel comparison, or RMSE computation.

Replacement envelopes have 48 header bytes: source bytes 0:32 unchanged (including all metadata bytes 16:32), `JXLCBG01` at 32:40, uint32 little-endian payload length at 40:44, and `00 00 58 4a` at 44:48. A replacement is accepted only when the entire envelope plus payload is strictly smaller than its source. This verifies envelope structure and preservation; it does not claim decoded-image equivalence.

Per-entry files live under `batch_fast\status\<archive-relative-path including .arc>\<index>.json` and `batch_fast\output\<archive-relative-path including .arc>\<index>.cbgjxl`. Keeping the full archive path prevents collisions between same-named archives in different directories. Retained output files contain original source bytes. Resume requires the current source hash and actual output hash to match the recorded SHA256 for both converted and retained entries. Error records are distinct from intentional retention and are retried. A run containing errors returns a nonzero exit status.

## Repack all archives

Python 3.10+ is required. Run from the repository root:

```powershell
python .\tools\repack_fast.py repack --root 'C:\work\CinderellaFD-task'
python .\tools\repack_fast.py repack --root 'C:\work\Cinderella2-task'
```

The default destination is `<task>\candidate_fast\`; use `--dest-root` to override it, and `--batch-dir` to override `<task>\batch_fast\`. Source and destination trees must be separate. Every source `.arc` is processed, including archives with no converted entries. By default every exact CBG entry must have a successful converted or retained status; missing statuses and explicit error statuses prevent publication. `--allow-incomplete` explicitly permits missing statuses for a limited trial, while requiring those entries to remain byte-identical. It never accepts error statuses.

Cinderella2's `HoukagoCinderella2_chs.arc` is a foreign EVB archive: its observed first eight bytes are `45 56 42 00 40 00 00 00` (`EVB\0` plus a 64-byte header size). This exact signature is allowlisted only for archives at least 64 bytes long. The repacker streams such files unchanged, independently verifies the entire file SHA256 and length before atomic publication, and inventories them with `kind: opaque`, source/output hashes, and a preservation reason. Conversion entry statuses for opaque archives are rejected. Arbitrary unknown signatures, truncated EVB headers, and malformed ARC20 files remain errors. The standalone `verify` command also supports these opaque EVB archives.

Repacking preserves the full 16-byte archive header and all bytes of every 128-byte index record except offsets and lengths at 96:104. It streams payloads in their original physical order, preserving leading gaps, interentry gaps, and trailer bytes. Zero-length entry anchors are relocated consistently; a zero-length anchor inside a replaced payload is rejected because its interior mapping is ambiguous. Overlapping nonempty payloads and out-of-bounds index/payload regions are rejected. Output offsets and lengths must fit ARC20 uint32 fields.

Each archive is written to a same-directory temporary file, flushed, and independently verified before atomic replacement of the destination. Verification checks raw records, expected physical layout, every unchanged payload SHA256, converted payload SHA256, exact source/envelope metadata, replacement size benefit, and separate SHA256 hashes for every gap and the trailer. Errors preserve an existing destination archive and remove the temporary file. Publication is atomic **per archive**, not for the whole game tree. Progress/errors are written atomically to `<task>\reports\fast_repack.json`; processing continues across independent archives and returns nonzero if any fail.

## Independently verify a candidate

```powershell
python .\tools\repack_fast.py verify --source 'C:\work\CinderellaFD-task\original\Archive\data.arc' --candidate 'C:\work\CinderellaFD-task\candidate_fast\Archive\data.arc' --archive 'Archive/data.arc' --batch-dir 'C:\work\CinderellaFD-task\batch_fast' --report 'C:\work\CinderellaFD-task\reports\verify_data.json'
```

`--archive` is the original-relative archive path recorded in the conversion statuses. Verification reads candidate payloads directly, so it does not need the conversion output files. Without `--report`, its JSON report prints to stdout. All CLI commands support `--help`.

## Synthetic smoke tests

```powershell
python -m unittest discover -s tools -p test_repack_fast.py -v
```

Tests synthesize a small ARC20 archive with reversed index/physical order, opaque index bytes, leading and interentry gaps, trailer bytes, zero-length anchors, one unchanged payload, and one smaller CBG envelope. They test preservation, payload/gap/trailer/record/header corruption rejection, metadata envelope corruption, strict size benefit, bounds/overlaps, missing status behavior, retained entries, status validation, and atomic failure cleanup. Additional foreign-archive tests check allowlisted EVB copying and hash verification, corruption rejection, conversion status rejection, and refusal of unknown signatures, truncated EVB headers, and malformed ARC20 files. The fixture uses an arbitrary fake JXL payload because these tests target container preservation and do not decode images or execute the encoder. Fixtures are created under the tools directory and cleaned up by the test runner.
