# Full JXL Batch Conversion Report

Deployment directory: `./jxl-release`
Original directory: `./original` (unmodified).

## Target Scope

Criteria:
- Asset type: CBG
- Header bpp: 24
- CBG type (u16 @ 46): 1
- Position / association flag (u16 @ 22): 0
- Extracted format: BGR24 (dimensions matching CBG header)

Total candidate images: 1,797
- Successfully converted: 1,796
- Retained in original format (no size benefit): 1

Compression parameters:
- Encoder: `cjxl` (libjxl)
- Quality: 90
- Effort: 8
- Maximum channel deviation: <= 1
- Maximum RMSE: <= 0.5669

## ARC20 Verification

- Packaged using streaming zero-loss repacker preserving all metadata headers, entry order, names, opaque indices, gaps, and trailers.
- Atomic swap via temporary file and fsync.
- All 205 archive files verified with SHA256 checksums.
- Original image payload: 4,095,128,954 bytes (~4.10 GB)
- JXL image payload: 393,828,016 bytes (~394 MB)
- Image payload savings: 3,701,300,938 bytes (90.24% reduction)
- Total game directory size: 9,634,913,179 bytes -> 5,933,612,241 bytes (38.42% overall reduction)
