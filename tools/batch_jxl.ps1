param(
    [int]$Worker = 0,
    [int]$Workers = 1,
    [int]$Limit = 0,
    [string]$GameDir = '.',
    [string]$GARbroPath = $env:GARBRO_PATH,
    [string]$JxlBinDir = $env:JXL_BIN_DIR,
    [string]$DecoderPath = $env:JXL_DECODER_PATH
)
$ErrorActionPreference = 'Stop'
$root = if ([System.IO.Path]::IsPathRooted($GameDir)) { $GameDir } else { (Resolve-Path $GameDir).Path }
$gar = if ($GARbroPath) { $GARbroPath } else { (Get-Command 'GARbro' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source) }
if (!$gar) { $gar = Join-Path $PSScriptRoot 'GARbro' }
$jxl = if ($JxlBinDir) { $JxlBinDir } else { (Get-Command 'cjxl' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source | Split-Path -Parent) }
if (!$jxl) { $jxl = Join-Path $PSScriptRoot 'jxl' }
Add-Type -AssemblyName WindowsBase,PresentationCore
[Reflection.Assembly]::LoadFrom("$gar\GameRes.dll")|Out-Null
[Reflection.Assembly]::LoadFrom("$gar\ArcFormats.dll")|Out-Null

Add-Type -TypeDefinition @'
using System;
public static class PixelsCheck {
 public static int MaxDifference; public static double RMSE;
 public static bool Match(byte[] bmpPixels, byte[] engine, int channels) {
  MaxDifference = 0; double sum = 0;
  if (channels == 1) {
   if (engine.Length != 16 + bmpPixels.Length) return false;
   for (int i = 0, j = 16; i < bmpPixels.Length; i++, j++) {
    int d = Math.Abs(bmpPixels[i] - engine[j]);
    if (d > MaxDifference) MaxDifference = d;
    sum += d * d;
   }
   RMSE = Math.Sqrt(sum / bmpPixels.Length);
   return MaxDifference <= 3 && RMSE <= 1.0;
  }
  if (engine.Length != 16 + bmpPixels.Length / channels * 4) return false;
  if (channels == 3) {
   for (int i = 0, j = 16; i < bmpPixels.Length; i += 3, j += 4) {
    if (engine[j + 3] != 0) return false;
    for (int c = 0; c < 3; c++) {
     int d = Math.Abs(bmpPixels[i + c] - engine[j + c]);
     if (d > MaxDifference) MaxDifference = d;
     sum += d * d;
    }
   }
   RMSE = Math.Sqrt(sum / bmpPixels.Length);
   return MaxDifference <= 3 && RMSE <= 1.0;
  } else if (channels == 4) {
   for (int i = 0, j = 16; i < bmpPixels.Length; i += 4, j += 4) {
    for (int c = 0; c < 4; c++) {
     int d = Math.Abs(bmpPixels[i + c] - engine[j + c]);
     if (d > MaxDifference) MaxDifference = d;
     sum += d * d;
    }
   }
   RMSE = Math.Sqrt(sum / bmpPixels.Length);
   return MaxDifference <= 3 && RMSE <= 1.0;
  }
  return false;
 }
}
'@

function HashBytes([byte[]]$b){[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($b)).ToLower()}
function SavePNG($bitmap,[string]$path){$enc=New-Object Windows.Media.Imaging.PngBitmapEncoder;$enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap));$f=[IO.File]::Create($path);try{$enc.Save($f)}finally{$f.Dispose()}}

# Validate individual entries instead of excluding whole archives.
$excludeArchives = @()

$inv = Get-Content "$root\reports\archive_inventory.json" -Raw | ConvertFrom-Json
$fmt = New-Object GameRes.Formats.BGI.CompressedBGFormat
$decoderExe = if ($DecoderPath) { $DecoderPath } else { Join-Path $PSScriptRoot '..\build\jxl_decode_test.exe' }
if (!(Test-Path $decoderExe)) { throw "Decoder test not found: $decoderExe" }
$ordinal = 0
$handled = 0

foreach ($a in $inv) {
 if ($a.archive -in $excludeArchives) { continue }
 $hasCbg = $false
 foreach ($ce in $a.entries) { if ($ce.kind -eq 'cbg') { $hasCbg = $true; break } }
 if (!$hasCbg) { continue }
 $fs = [IO.File]::OpenRead((Join-Path "$root\original" $a.archive))
 try {
 foreach ($e in $a.entries) {
  if ($e.kind -ne 'cbg') { continue }
  $bpp = $e.image_header_u16[2]
  if ($bpp -ne 8 -and $bpp -ne 24 -and $bpp -ne 32) { continue }

  $slot = $ordinal
  $ordinal++
  if ($slot % $Workers -ne $Worker) { continue }
  if ($Limit -gt 0 -and $handled -ge $Limit) { break }
  $handled++

  $stem = [IO.Path]::GetFileNameWithoutExtension($a.archive)
  $dir = "$root\batch\output\$stem"
  $statusdir = "$root\batch\status"
  $tmp = "$root\batch\work\worker$Worker"
  foreach ($d in @($dir, $statusdir, $tmp)) { [IO.Directory]::CreateDirectory($d) | Out-Null }

  $dest = "$dir\$($e.index).cbgjxl"
  $statusfile = "$statusdir\${stem}_$($e.index).json"

  $fs.Position = [int64]$e.absolute
  $raw = New-Object byte[] $e.length
  $fs.ReadExactly($raw, 0, $raw.Length)
  $sha = HashBytes $raw

  # Check resume
  if (Test-Path $statusfile) {
   $prior = Get-Content $statusfile -Raw | ConvertFrom-Json
   if ($prior.source_sha256 -eq $sha -and $prior.archive -eq $a.archive -and [int]$prior.index -eq [int]$e.index -and (($prior.status -eq 'retained') -or ($prior.status -eq 'converted' -and (Test-Path $dest) -and (Get-FileHash $dest).Hash.ToLower() -eq $prior.output_sha256))) {
    continue
   }
  }

  $row = [ordered]@{
   archive = $a.archive; index = [int]$e.index; name = $e.name; source_sha256 = $sha;
   original_bytes = [int]$e.length; new_bytes = [int]$e.length; status = 'retained'; reason = '';
   quality = 90; effort = 7; output_sha256 = $sha; decode_verified = $false;
   policy_version = 'opaque-type1-v1'; validation_schema = 'jxl-cbg-v2';
   decoder_max_channel_difference = 0; decoder_rmse = 0
  }

  try {
   if ([BitConverter]::ToUInt16($raw, 22) -ne 0 -or [BitConverter]::ToUInt32($raw, 24) -ne 0 -or [BitConverter]::ToUInt32($raw, 28) -ne 0) { throw 'Nonzero layout/association/unknown metadata: retain' }
   $ms = New-Object IO.MemoryStream(,$raw)
   try {
    $bs = New-Object GameRes.BinaryStream($ms, $e.name, $false)
    $md = $fmt.ReadMetaData($bs)
    $ms.Position = 0
    $img = $fmt.Read($bs, $md)
   } finally {
    $ms.Dispose()
   }
   $bitmap = $img.Bitmap
   $fmtName = $bitmap.Format.ToString()
   $formatOk = if ($bpp -eq 24) { $fmtName -in @('Bgr24','Bgr32') } elseif ($bpp -eq 32) { $fmtName -eq 'Bgra32' } else { $fmtName -eq 'Gray8' }
   if (!$formatOk -or $bitmap.PixelWidth -ne $e.image_header_u16[0] -or $bitmap.PixelHeight -ne $e.image_header_u16[1]) {
    throw "PixelFormat/dimensions not supported: retain ($($bitmap.Format))"
   }

   if ($bpp -eq 24 -and $fmtName -eq 'Bgr32') {
     $converted = New-Object Windows.Media.Imaging.FormatConvertedBitmap
     $converted.BeginInit(); $converted.Source = $bitmap; $converted.DestinationFormat = [Windows.Media.PixelFormats]::Bgr24; $converted.EndInit()
     $bitmap = $converted
    }

    $channels = if ($bpp -eq 24) { 3 } elseif ($bpp -eq 32) { 4 } else { 1 }
   $png = "$tmp\input.png"; $encoded = "$tmp\encoded.jxl"; $decoded = "$tmp\decoded.png"; $envelope = "$tmp\envelope.cbgjxl"; $engine = "$tmp\engine.raw"
   SavePNG $bitmap $png

   & "$jxl\cjxl.exe" $png $encoded -q 90 -e 7 --num_threads=2 2> "$tmp\encode.log"
   if ($LASTEXITCODE) { throw 'cjxl failed' }

   $payload = [IO.File]::ReadAllBytes($encoded)
   if ($payload.Length + 48 -ge $raw.Length) {
    $row.reason = 'No size benefit'
   } else {
    $header = New-Object byte[] 48
    [Array]::Copy($raw, $header, 48)
    [Array]::Copy([Text.Encoding]::ASCII.GetBytes('JXLCBG01'), 0, $header, 32, 8)
    [Array]::Copy([BitConverter]::GetBytes([uint32]$payload.Length), 0, $header, 40, 4)
    $header[44] = 0; $header[45] = 0; $header[46] = 0x58; $header[47] = 0x4a
    $env = New-Object byte[] (48 + $payload.Length)
    [Array]::Copy($header, $env, 48)
    [Array]::Copy($payload, 0, $env, 48, $payload.Length)
    [IO.File]::WriteAllBytes($envelope, $env)

    & "$jxl\djxl.exe" $encoded $decoded --num_threads=1 2> "$tmp\decode.log"
    if ($LASTEXITCODE) { throw 'djxl failed' }

    & $decoderExe $envelope $engine > "$tmp\engine.log"
    if ($LASTEXITCODE) { throw 'Native decoder failed' }

    $df = [IO.File]::OpenRead($decoded)
    try {
     $pngDecoder = [Windows.Media.Imaging.BitmapDecoder]::Create($df, [Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat, [Windows.Media.Imaging.BitmapCacheOption]::OnLoad)
     $bmp = $pngDecoder.Frames[0]
    } finally {
     $df.Dispose()
    }
    $targetPixFmt = if ($channels -eq 3) { [Windows.Media.PixelFormats]::Bgr24 } elseif ($channels -eq 4) { [Windows.Media.PixelFormats]::Bgra32 } else { [Windows.Media.PixelFormats]::Gray8 }
    $conv = New-Object Windows.Media.Imaging.FormatConvertedBitmap
    $conv.BeginInit(); $conv.Source = $bmp; $conv.DestinationFormat = $targetPixFmt; $conv.EndInit()

    if ($conv.PixelWidth -ne $bitmap.PixelWidth -or $conv.PixelHeight -ne $bitmap.PixelHeight) {
     throw 'Decoded dimensions mismatch'
    }

    $stride = $conv.PixelWidth * $channels
    $pixels = New-Object byte[] ($conv.PixelHeight * $stride)
    $conv.CopyPixels($pixels, $stride, 0)
    $enginebytes = [IO.File]::ReadAllBytes($engine)

    if (![PixelsCheck]::Match($pixels, $enginebytes, $channels)) {
     throw 'Independent and native decoded pixels differ beyond tolerance'
    }

    if ($bpp -eq 24) {
     for ($k = 0; $k -lt 16; $k++) {
      if ($k -in 4, 5, 8, 9) { continue }
      if ($enginebytes[$k] -ne $raw[16 + $k]) { throw 'Position/metadata changed' }
     }
    } else {
     for ($k = 0; $k -lt 16; $k++) {
      if ($enginebytes[$k] -ne $raw[16 + $k]) { throw 'Position/metadata changed' }
     }
    }

    [IO.File]::WriteAllBytes("$dest.partial", $env)
    Move-Item "$dest.partial" $dest -Force
    $row.status = 'converted'
    $row.reason = 'smaller, validated cross-version decode'
    $row.new_bytes = $env.Length
    $row.output_sha256 = HashBytes $env
    $row.decode_verified = $true
    $row.decoder_max_channel_difference = [PixelsCheck]::MaxDifference
    $row.decoder_rmse = [PixelsCheck]::RMSE
   }
  } catch {
   $row.reason = $_.Exception.Message
  }

  $row | ConvertTo-Json | Set-Content "$statusfile.partial" -Encoding utf8
  Move-Item "$statusfile.partial" $statusfile -Force
  if ($handled % 20 -eq 0) { [GC]::Collect() }
 }
 } finally {
  $fs.Dispose()
 }
}
