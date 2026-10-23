# Windows PowerShell 5.1 / .NET Framework. Only source CBG images are read;
# validation covers the output envelope and hashes, without a JXL round trip.
[CmdletBinding()]
param(
    [ValidateRange(0, 2147483647)][int]$Worker = 0,
    [ValidateRange(1, 2147483647)][int]$Workers = 1,
    [ValidateRange(0, 2147483647)][int]$Limit = 0,
    [string]$GameDir = '.',
    [string]$GARbroPath = $env:GARBRO_PATH,
    [string]$CjxlPath = $env:CJXL_PATH
)

$ErrorActionPreference = 'Stop'
$policyVersion = 'cbg-fast-q90-e7-t2-v1'
$validationSchema = 'cbg-envelope-v1'
$cbgMagic = [Text.Encoding]::ASCII.GetBytes("CompressedBG___`0")
$envelopeMagic = [Text.Encoding]::ASCII.GetBytes('JXLCBG01')

function Read-BytesExactly([IO.Stream]$Stream, [int]$Count) {
    $bytes = New-Object byte[] $Count
    $position = 0
    while ($position -lt $Count) {
        $read = $Stream.Read($bytes, $position, $Count - $position)
        if ($read -eq 0) { throw 'Unexpected end of archive' }
        $position += $read
    }
    return ,$bytes
}

function Get-BytesHash([byte[]]$Bytes) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Get-OutputHash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}

function Test-BytePrefix([byte[]]$Bytes, [byte[]]$Prefix) {
    if ($Bytes.Length -lt $Prefix.Length) { return $false }
    for ($i = 0; $i -lt $Prefix.Length; $i++) {
        if ($Bytes[$i] -ne $Prefix[$i]) { return $false }
    }
    return $true
}

function New-Status([string]$Archive, [long]$Index, [string]$Name, [long]$Length) {
    return [ordered]@{
        archive = $Archive; index = $Index; name = $Name
        source_sha256 = $null; output_sha256 = $null
        original_bytes = $Length; new_bytes = $Length
        status = 'error'; reason = ''
        policy_version = $policyVersion; validation_schema = $validationSchema
        quality = 90; effort = 7; threads = 2
    }
}

function Save-Status($Row, [string]$Path) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path)) | Out-Null
    $json = $Row | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText("$Path.partial", $json, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath "$Path.partial" -Destination $Path -Force
}

function Save-Output([byte[]]$Bytes, [string]$Path) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path)) | Out-Null
    [IO.File]::WriteAllBytes("$Path.partial", $Bytes)
    Move-Item -LiteralPath "$Path.partial" -Destination $Path -Force
}

function Test-Resume([string]$StatusPath, [string]$OutputPath, $Row) {
    if (![IO.File]::Exists($StatusPath) -or ![IO.File]::Exists($OutputPath)) { return $false }
    try {
        $prior = [IO.File]::ReadAllText($StatusPath) | ConvertFrom-Json
        if ($prior.status -ne 'converted' -and $prior.status -ne 'retained') { return $false }
        if ($prior.reason -like 'Unsupported source pixel format:*') { return $false }
        if ($prior.policy_version -ne $policyVersion -or $prior.validation_schema -ne $validationSchema -or
            $prior.quality -ne 90 -or $prior.effort -ne 7 -or $prior.threads -ne 2 -or
            $prior.archive -cne $Row.archive -or $prior.index -ne $Row.index -or $prior.name -cne $Row.name -or
            $prior.source_sha256 -cne $Row.source_sha256 -or $prior.original_bytes -ne $Row.original_bytes) { return $false }
        $output = Get-Item -LiteralPath $OutputPath
        if ($output.Length -ne $prior.new_bytes) { return $false }
        if ($prior.status -eq 'retained' -and
            ($prior.output_sha256 -cne $Row.source_sha256 -or $prior.new_bytes -ne $Row.original_bytes)) { return $false }
        if ($prior.status -eq 'converted' -and $prior.new_bytes -ge $Row.original_bytes) { return $false }
        return ((Get-OutputHash $OutputPath) -ceq $prior.output_sha256)
    } catch { return $false }
}

function Save-Png($Bitmap, [string]$Path) {
    $encoder = New-Object Windows.Media.Imaging.PngBitmapEncoder
    $encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($Bitmap))
    $stream = [IO.File]::Create($Path)
    try { $encoder.Save($stream) } finally { $stream.Dispose() }
}

function New-Envelope([byte[]]$Source, [byte[]]$Payload) {
    if ($Source.Length -lt 32 -or $Payload.Length -eq 0) { throw 'Missing CBG header or empty JXL payload' }
    if ([long]$Payload.Length + 48 -gt [int]::MaxValue) { throw 'JXL envelope exceeds supported buffer size' }
    $result = New-Object byte[] (48 + $Payload.Length)
    [Array]::Copy($Source, 0, $result, 0, 32)
    [Array]::Copy($envelopeMagic, 0, $result, 32, 8)
    [Array]::Copy([BitConverter]::GetBytes([uint32]$Payload.Length), 0, $result, 40, 4)
    $result[44] = 0; $result[45] = 0; $result[46] = 0x58; $result[47] = 0x4a
    [Array]::Copy($Payload, 0, $result, 48, $Payload.Length)
    for ($i = 0; $i -lt 32; $i++) {
        if ($result[$i] -ne $Source[$i]) { throw 'CBG header preservation failed' }
    }
    for ($i = 0; $i -lt 8; $i++) {
        if ($result[32 + $i] -ne $envelopeMagic[$i]) { throw 'Envelope magic mismatch' }
    }
    if ([BitConverter]::ToUInt32($result, 40) -ne $result.Length - 48 -or
        $result[44] -ne 0 -or $result[45] -ne 0 -or $result[46] -ne 0x58 -or $result[47] -ne 0x4a) {
        throw 'Envelope length or marker mismatch'
    }
    return ,$result
}

function Invoke-Cjxl([string]$InputPath, [string]$OutputPath, [string]$LogPath) {
    if ([IO.File]::Exists($OutputPath)) { [IO.File]::Delete($OutputPath) }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $CjxlPath
    $info.Arguments = '"{0}" "{1}" -q 90 -e 7 --num_threads=2' -f $InputPath, $OutputPath
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (!$process.Start()) { throw 'Could not start cjxl' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [IO.File]::WriteAllText($LogPath, ($stdout.Result + $stderr.Result))
        if ($process.ExitCode -ne 0) { throw "cjxl failed with exit code $($process.ExitCode); see $LogPath" }
        if (![IO.File]::Exists($OutputPath)) { throw 'cjxl produced no output' }
    } finally { $process.Dispose() }
}

try {
    if ($Worker -ge $Workers) { throw 'Worker must be less than Workers' }
    $root = (Resolve-Path -LiteralPath $GameDir).Path
    $original = Join-Path $root 'original'
    if (![IO.Directory]::Exists($original)) { throw "Missing original directory: $original" }
    $originalPrefix = [IO.Path]::GetFullPath($original).TrimEnd('\') + '\'
    $batch = Join-Path $root 'batch_fast'
    $work = Join-Path $batch ("work\worker{0}" -f $Worker)
    $archives = @(Get-ChildItem -LiteralPath $original -Filter '*.arc' -File -Recurse | Sort-Object FullName)
    if ($archives.Count -eq 0) { throw "No .arc archives found under: $original" }
    $nameEncoding = [Text.Encoding]::GetEncoding(932)
    $archiveMagic = [Text.Encoding]::ASCII.GetBytes('BURIKO ARC20')
    $format = $null
    [long]$ordinal = 0
    $handled = 0; $resumed = 0; $errors = 0
    $limitReached = $false

    foreach ($archive in $archives) {
        if ($limitReached) { break }
        $relative = $archive.FullName.Substring($originalPrefix.Length).Replace('\', '/')
        $outputDir = Join-Path (Join-Path $batch 'output') $relative
        $statusDir = Join-Path (Join-Path $batch 'status') $relative
        $stream = $null
        try {
            $stream = [IO.File]::OpenRead($archive.FullName)
            $header = Read-BytesExactly $stream 16
            if (!(Test-BytePrefix $header $archiveMagic)) {
                # Translation resource archive: confirmed Enigma Virtual Box EVB header (64 bytes).
                $evbMagic = [byte[]](0x45, 0x56, 0x42, 0, 0x40, 0, 0, 0)
                if ($stream.Length -ge 64 -and (Test-BytePrefix $header $evbMagic)) {
                    $stale = Join-Path $statusDir '-1.json'
                    if ([IO.File]::Exists($stale)) { [IO.File]::Delete($stale) }
                    Write-Host "Preserving opaque EVB translation archive: $relative"
                    continue
                }
                throw 'Not a supported ARC20 or opaque EVB archive'
            }
            [long]$count = [BitConverter]::ToUInt32($header, 12)
            [long]$dataStart = 16 + $count * 128
            if ($dataStart -gt $stream.Length) { throw 'ARC20 index extends beyond archive' }
            # A previous archive-level failure is superseded by this valid header/index.
            # Delete is idempotent if another worker already cleared the shared sentinel.
            $archiveErrorPath = Join-Path $statusDir '-1.json'
            if ([IO.File]::Exists($archiveErrorPath)) { [IO.File]::Delete($archiveErrorPath) }
            for ([long]$index = 0; $index -lt $count; $index++) {
                if ($Limit -gt 0 -and $handled -ge $Limit) { $limitReached = $true; break }
                $stream.Position = 16 + $index * 128
                $record = Read-BytesExactly $stream 128
                $nameLength = 0
                while ($nameLength -lt 96 -and $record[$nameLength] -ne 0) { $nameLength++ }
                $name = $nameEncoding.GetString($record, 0, $nameLength)
                [long]$offset = [BitConverter]::ToUInt32($record, 96)
                [long]$length = [BitConverter]::ToUInt32($record, 100)
                $dest = Join-Path $outputDir "$index.cbgjxl"
                $statusPath = Join-Path $statusDir "$index.json"
                $row = New-Status $relative $index $name $length
                try {
                    if ($dataStart + $offset + $length -gt $stream.Length) { throw 'ARC20 entry extends beyond archive' }
                    if ($length -lt 16) { continue }
                    $stream.Position = $dataStart + $offset
                    $prefix = Read-BytesExactly $stream ([int][Math]::Min(32, $length))
                    if (!(Test-BytePrefix $prefix $cbgMagic)) { continue }
                    $slot = $ordinal; $ordinal++
                    if ($slot % $Workers -ne $Worker) { continue }
                    $handled++
                    if ($length -gt [int]::MaxValue) { throw 'CBG entry exceeds supported buffer size' }
                    $stream.Position = $dataStart + $offset
                    $raw = Read-BytesExactly $stream ([int]$length)
                    $row.source_sha256 = Get-BytesHash $raw
                    if (Test-Resume $statusPath $dest $row) { $resumed++; continue }
                    if ($raw.Length -lt 32) { throw 'Truncated CBG header' }
                    $depth = [BitConverter]::ToUInt16($raw, 20)
                    $flag = [BitConverter]::ToUInt16($raw, 22)
                    $row.status = 'retained'
                    $outputBytes = $raw
                    if ($depth -ne 24 -and $depth -ne 32) {
                        $row.reason = "Unsupported bit depth: $depth"
                    } elseif ($flag -ne 0) {
                        $row.reason = "Unsupported layout/association flag: $flag"
                    } else {
                        if ($null -eq $format) {
                            if (!$GARbroPath) {
                                $garCmd = Get-Command 'GARbro' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
                                $GARbroPath = if ($garCmd) { Split-Path -Parent $garCmd } else { Join-Path $PSScriptRoot 'GARbro' }
                            }
                            if (!$CjxlPath) {
                                $cjxlCmd = Get-Command 'cjxl' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
                                $CjxlPath = if ($cjxlCmd) { $cjxlCmd } else { Join-Path $PSScriptRoot 'jxl\cjxl.exe' }
                            }
                            Add-Type -AssemblyName WindowsBase, PresentationCore
                            [Reflection.Assembly]::LoadFrom((Join-Path $GARbroPath 'GameRes.dll')) | Out-Null
                            [Reflection.Assembly]::LoadFrom((Join-Path $GARbroPath 'ArcFormats.dll')) | Out-Null
                            if (![IO.File]::Exists($CjxlPath)) { throw "Missing cjxl executable: $CjxlPath" }
                            $format = New-Object GameRes.Formats.BGI.CompressedBGFormat
                            [IO.Directory]::CreateDirectory($work) | Out-Null
                        }
                        $memory = New-Object IO.MemoryStream(,$raw)
                        $binary = $null
                        try {
                            $binary = New-Object GameRes.BinaryStream($memory, $name, $false)
                            $metadata = $format.ReadMetaData($binary)
                            if ($null -eq $metadata) { throw 'GARbro could not read CBG metadata' }
                            $memory.Position = 0
                            $image = $format.Read($binary, $metadata)
                            $bitmap = $image.Bitmap
                        } finally {
                            if ($null -ne $binary) { $binary.Dispose() }
                            $memory.Dispose()
                        }
                        $expectedFormat = if ($depth -eq 24) { 'Bgr24' } else { 'Bgra32' }
                        # GARbro legitimately emits opaque Bgr32 for 24-bit CBG.
                        # WPF's native pixel-format conversion is source export, not a decoded-image comparison.
                        if ($depth -eq 24 -and $bitmap.Format.ToString() -eq 'Bgr32') {
                            $converted = New-Object Windows.Media.Imaging.FormatConvertedBitmap
                            $converted.BeginInit(); $converted.Source = $bitmap
                            $converted.DestinationFormat = [Windows.Media.PixelFormats]::Bgr24
                            $converted.EndInit(); $converted.Freeze(); $bitmap = $converted
                        }
                        if ($bitmap.Format.ToString() -ne $expectedFormat) {
                            $row.reason = "Unsupported source pixel format: $($bitmap.Format)"
                        } else {
                            if ($bitmap.PixelWidth -ne [BitConverter]::ToUInt16($raw, 16) -or
                                $bitmap.PixelHeight -ne [BitConverter]::ToUInt16($raw, 18)) { throw 'Source CBG dimensions disagree with header' }
                            $png = Join-Path $work 'input.png'
                            $encoded = Join-Path $work 'encoded.jxl'
                            Save-Png $bitmap $png
                            Invoke-Cjxl $png $encoded (Join-Path $work 'encode.log')
                            $payload = [IO.File]::ReadAllBytes($encoded)
                            if ($payload.Length -eq 0) { throw 'cjxl produced an empty payload' }
                            if ([long]$payload.Length + 48 -ge $raw.Length) {
                                $row.reason = 'No strict size benefit'
                            } else {
                                $outputBytes = New-Envelope $raw $payload
                                $row.status = 'converted'
                                $row.reason = 'Smaller; CBG header and JXL envelope validated'
                            }
                        }
                    }
                    Save-Output $outputBytes $dest
                    $row.new_bytes = $outputBytes.Length
                    $row.output_sha256 = Get-BytesHash $outputBytes
                } catch {
                    $row.status = 'error'; $row.reason = $_.Exception.Message
                    $row.output_sha256 = $null; $row.new_bytes = 0
                    $errors++
                    if ([IO.File]::Exists($dest)) { [IO.File]::Delete($dest) }
                    Write-Warning ("{0}:{1}: {2}" -f $relative, $index, $row.reason)
                }
                Save-Status $row $statusPath
                if ($handled -gt 0 -and $handled % 20 -eq 0) { [GC]::Collect() }
            }
        } catch {
            $errors++
            $row = New-Status $relative -1 '' 0
            $row.new_bytes = 0; $row.reason = $_.Exception.Message
            Save-Status $row (Join-Path $statusDir '-1.json')
            Write-Warning ("{0}: {1}" -f $relative, $row.reason)
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
    }
    Write-Host ("Worker {0}/{1}: handled={2}, resumed={3}, errors={4}" -f $Worker, $Workers, $handled, $resumed, $errors)
    if ($errors -gt 0) { exit 1 }
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
