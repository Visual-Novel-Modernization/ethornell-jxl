"""Stream ARC20 repack and independent preservation verification (no image decoding).

See fast_tools.md for CLI examples. Only batch_fast's explicit envelope policy is
accepted. Every candidate is verified before its temporary file is published.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import tempfile

MAGIC = b'BURIKO ARC20'
CBG = b'CompressedBG___\0'
POLICY = 'cbg-fast-q90-e7-t2-v1'
SCHEMA = 'cbg-envelope-v1'
CHUNK = 8 * 1024 * 1024


@dataclass
class Entry:
    index: int
    record: bytes
    offset: int
    length: int


def parse(path):
    size = path.stat().st_size
    with path.open('rb') as f:
        header = f.read(16)
        if len(header) != 16 or header[:12] != MAGIC:
            raise ValueError(f'not ARC20: {path}')
        count = struct.unpack_from('<I', header, 12)[0]
        base = 16 + count * 128
        if base > size:
            raise ValueError(f'index outside archive: {path}')
        entries = []
        for i in range(count):
            rec = f.read(128)
            off, length = struct.unpack_from('<II', rec, 96)
            if base + off + length > size:
                raise ValueError(f'entry {i} outside archive: {path}')
            entries.append(Entry(i, rec, off, length))
    cursor = 0
    for e in physical(entries):
        if e.offset < cursor:
            raise ValueError(f'overlapping payload {e.index}: {path}')
        cursor = e.offset + e.length
    return header, entries, base, size


def physical(entries):
    return sorted((e for e in entries if e.length), key=lambda e: (e.offset, e.index))


def digest(f, start, length, target=None):
    h = hashlib.sha256()
    f.seek(start)
    while length:
        data = f.read(min(CHUNK, length))
        if not data:
            raise ValueError('unexpected EOF')
        h.update(data)
        if target is not None:
            target.write(data)
        length -= len(data)
    return h.hexdigest()


def safe_rel(text):
    rel = PurePosixPath(str(text).replace('\\', '/'))
    if rel.is_absolute() or not rel.parts or any(x in ('.', '..') or ':' in x for x in rel.parts):
        raise ValueError(f'unsafe archive path: {text}')
    return rel.as_posix()


def statuses(batch):
    result = {}
    for file in sorted((batch / 'status').rglob('*.json')):
        row = json.loads(file.read_text(encoding='utf-8-sig'))
        required = ('archive', 'index', 'name', 'status', 'source_sha256', 'output_sha256',
                    'original_bytes', 'new_bytes', 'policy_version', 'validation_schema')
        if not isinstance(row, dict) or any(k not in row for k in required):
            raise ValueError(f'incomplete status: {file}')
        if row['policy_version'] != POLICY or row['validation_schema'] != SCHEMA:
            raise ValueError(f'incompatible status policy: {file}')
        if row['status'] not in ('converted', 'retained'):
            raise ValueError(f'conversion error or invalid status in {file}: {row.get("reason")}')
        rel = safe_rel(row['archive'])
        idx = row['index']
        if type(idx) is not int or idx < 0:
            raise ValueError(f'invalid entry index: {file}')
        for field in ('source_sha256', 'output_sha256'):
            value = row[field]
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'invalid {field}: {file}')
        slot = result.setdefault(rel, {})
        if idx in slot:
            raise ValueError(f'duplicate status: {rel}:{idx}')
        slot[idx] = row
    return result


def envelope(source_head, source_length, head, length):
    if len(source_head) < 32 or source_head[:16] != CBG:
        raise ValueError('replacement source is not exact CBG')
    width, height, depth, flag = struct.unpack_from('<4H', source_head, 16)
    if not width or not height or depth not in (24, 32) or flag != 0:
        raise ValueError('unsupported replacement metadata')
    if length < 49 or len(head) < 48 or head[:32] != source_head[:32]:
        raise ValueError('CBG magic/metadata bytes 0:32 changed')
    if head[32:40] != b'JXLCBG01' or head[44:48] != b'\0\0XJ':
        raise ValueError('invalid 48-byte envelope marker')
    if struct.unpack_from('<I', head, 40)[0] != length - 48:
        raise ValueError('envelope payload length mismatch')
    if length >= source_length:
        raise ValueError('replacement provides no strict size benefit')


def selections(source, entries, base, selected, batch, rel, allow_incomplete=False):
    if set(selected) - {e.index for e in entries}:
        raise ValueError(f'status index outside {rel}')
    replacements = {}
    with source.open('rb') as f:
        for e in entries:
            f.seek(base + e.offset)
            head = f.read(min(48, e.length))
            row = selected.get(e.index)
            if row is None:
                if head[:16] == CBG and not allow_incomplete:
                    raise ValueError(f'missing CBG status: {rel}:{e.index}')
                continue
            name = e.record[:96].split(b'\0', 1)[0].decode('cp932', errors='replace')
            if row['name'] != name or row['original_bytes'] != e.length:
                raise ValueError(f'status metadata mismatch: {rel}:{e.index}')
            if digest(f, base + e.offset, e.length) != row['source_sha256']:
                raise ValueError(f'source hash mismatch: {rel}:{e.index}')
            if row['status'] == 'retained':
                if row['new_bytes'] != e.length or row['output_sha256'] != row['source_sha256']:
                    raise ValueError(f'invalid retained status: {rel}:{e.index}')
                continue
            if (row.get('quality'), row.get('effort'), row.get('threads')) != (90, 7, 2):
                raise ValueError(f'wrong encoder settings: {rel}:{e.index}')
            path = batch / 'output' / Path(rel) / f'{e.index}.cbgjxl'
            length = path.stat().st_size
            with path.open('rb') as replacement:
                envelope(head, e.length, replacement.read(48), length)
                if length != row['new_bytes'] or digest(replacement, 0, length) != row['output_sha256']:
                    raise ValueError(f'replacement hash/length mismatch: {path}')
            replacements[e.index] = path
    return replacements


def layout(entries, lengths, data_size):
    """Return index offsets and opaque regions in physical source order."""
    offsets, regions = {}, []
    old_cursor = new_cursor = 0
    ordered = physical(entries)
    for e in ordered:
        gap = e.offset - old_cursor
        regions.append((old_cursor, new_cursor, gap, 'leading' if old_cursor == 0 else 'gap'))
        new_cursor += gap
        offsets[e.index] = new_cursor
        new_cursor += lengths[e.index]
        old_cursor = e.offset + e.length
    regions.append((old_cursor, new_cursor, data_size - old_cursor, 'trailer'))
    total = new_cursor + data_size - old_cursor
    for e in entries:
        if e.length:
            continue
        delta = 0
        for p in ordered:
            if p.offset < e.offset < p.offset + p.length:
                if lengths[p.index] != p.length:
                    raise ValueError(f'zero-length entry {e.index} inside replaced payload')
                break
            if p.offset + p.length <= e.offset:
                delta += lengths[p.index] - p.length
        offsets[e.index] = e.offset + delta
    if any(not 0 <= offsets[e.index] <= 0xffffffff or not 0 <= lengths[e.index] <= 0xffffffff for e in entries):
        raise ValueError('output offset/length exceeds ARC20 uint32')
    return offsets, regions, total


def archive_kind(path):
    """Recognize foreign archives without hiding damaged ARC20 headers."""
    with path.open('rb') as f:
        head = f.read(16)
    if head[:12] == MAGIC:
        return 'arc20'  # parse() remains responsible for all ARC20 validity checks.
    # Cinderella2's translation archive is EVB, with a 64-byte header.
    # Only this observed foreign signature is allowlisted; arbitrary magic is an error.
    if len(head) == 16 and head[:8] == b'EVB\0@\0\0\0' and path.stat().st_size >= 64:
        return 'opaque'
    raise ValueError(f'unknown or truncated archive header: {path}')


def verify_opaque(source, candidate, selected, rel):
    if selected:
        raise ValueError(f'opaque archive must have no conversion entry statuses: {rel}')
    source_size = source.stat().st_size
    if candidate.stat().st_size != source_size:
        raise ValueError(f'opaque archive size mismatch: {rel}')
    with source.open('rb') as sf, candidate.open('rb') as cf:
        original = digest(sf, 0, source_size)
        output = digest(cf, 0, source_size)
    if original != output:
        raise ValueError(f'opaque archive SHA256 mismatch: {rel}')
    return dict(archive=rel, kind='opaque', verified=True, source_bytes=source_size,
                output_bytes=source_size, source_sha256=original, output_sha256=output,
                preservation='entire foreign archive unchanged', entries=[])


def copy_opaque(source, destination, selected, rel):
    if source.resolve() == destination.resolve():
        raise ValueError('destination must differ from source')
    if selected:
        raise ValueError(f'opaque archive must have no conversion entry statuses: {rel}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=destination.name + '.', suffix='.partial', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as dst, source.open('rb') as src:
            digest(src, 0, source.stat().st_size, dst)
            dst.flush()
            os.fsync(dst.fileno())
        report = verify_opaque(source, temporary, selected, rel)
        os.replace(temporary, destination)
        return report
    finally:
        temporary.unlink(missing_ok=True)


def verify(source, candidate, selected, rel, allow_incomplete=False):
    if archive_kind(source) == 'opaque':
        return verify_opaque(source, candidate, selected, rel)
    header, old, base, source_size = parse(source)
    other_header, new, new_base, candidate_size = parse(candidate)
    if header != other_header or base != new_base:
        raise ValueError('archive header changed')
    lengths = {e.index: e.length for e in new}
    offsets, regions, total = layout(old, lengths, source_size - base)
    if candidate_size != base + total:
        raise ValueError('candidate size/trailer extent mismatch')
    payload_rows, region_rows = [], []
    with source.open('rb') as sf, candidate.open('rb') as cf:
        for a, b in zip(old, new):
            if a.record[:96] != b.record[:96] or a.record[104:] != b.record[104:]:
                raise ValueError(f'raw index record changed: {a.index}')
            if b.offset != offsets[a.index]:
                raise ValueError(f'physical layout changed: {a.index}')
            sf.seek(base + a.offset)
            old_head = sf.read(min(a.length, 48))
            row = selected.get(a.index)
            if row is None and old_head[:16] == CBG and not allow_incomplete:
                raise ValueError(f'missing CBG status: {rel}:{a.index}')
            source_hash = digest(sf, base + a.offset, a.length)
            output_hash = digest(cf, base + b.offset, b.length)
            status = row['status'] if row else 'unchanged'
            if row:
                name = a.record[:96].split(b'\0', 1)[0].decode('cp932', errors='replace')
                if row['source_sha256'] != source_hash or row['original_bytes'] != a.length or row['name'] != name:
                    raise ValueError(f'source/status mismatch: {a.index}')
                if row['output_sha256'] != output_hash or row['new_bytes'] != b.length:
                    raise ValueError(f'output/status mismatch: {a.index}')
            if status == 'converted':
                if (row.get('quality'), row.get('effort'), row.get('threads')) != (90, 7, 2):
                    raise ValueError(f'wrong encoder settings: {a.index}')
                cf.seek(base + b.offset)
                envelope(old_head, a.length, cf.read(min(48, b.length)), b.length)
            elif a.length != b.length or source_hash != output_hash:
                raise ValueError(f'unchanged payload mismatch: {a.index}')
            payload_rows.append(dict(index=a.index, status=status, source_sha256=source_hash, output_sha256=output_hash))
        for old_start, new_start, length, kind in regions:
            original = digest(sf, base + old_start, length)
            rebuilt = digest(cf, base + new_start, length)
            if original != rebuilt:
                raise ValueError(f'{kind} hash mismatch at source offset {old_start}')
            region_rows.append(dict(kind=kind, source_offset=old_start, output_offset=new_start,
                                    length=length, sha256=original))
    if set(selected) - {e.index for e in old}:
        raise ValueError('status index outside archive')
    return dict(archive=rel, verified=True, source_bytes=source_size, output_bytes=candidate_size,
                entries=payload_rows, opaque_regions=region_rows)


def repack(source, destination, selected, batch, rel, allow_incomplete=False):
    if archive_kind(source) == 'opaque':
        return copy_opaque(source, destination, selected, rel)
    if source.resolve() == destination.resolve():
        raise ValueError('destination must differ from source')
    header, entries, base, size = parse(source)
    replacements = selections(source, entries, base, selected, batch, rel, allow_incomplete)
    lengths = {e.index: replacements[e.index].stat().st_size if e.index in replacements else e.length for e in entries}
    offsets, _, total = layout(entries, lengths, size - base)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=destination.name + '.', suffix='.partial', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as dst, source.open('rb') as src:
            dst.write(header)
            for e in entries:
                record = bytearray(e.record)
                struct.pack_into('<II', record, 96, offsets[e.index], lengths[e.index])
                dst.write(record)
            cursor = 0
            for e in physical(entries):
                digest(src, base + cursor, e.offset - cursor, dst)
                if e.index in replacements:
                    with replacements[e.index].open('rb') as replacement:
                        digest(replacement, 0, lengths[e.index], dst)
                else:
                    digest(src, base + e.offset, e.length, dst)
                cursor = e.offset + e.length
            digest(src, base + cursor, size - base - cursor, dst)
            dst.flush()
            os.fsync(dst.fileno())
        if temporary.stat().st_size != base + total:
            raise ValueError('streamed size mismatch')
        report = verify(source, temporary, selected, rel, allow_incomplete)
        os.replace(temporary, destination)
        return report
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.partial', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    batch_parser = commands.add_parser('repack', help='repack ALL source archives and verify before atomic publication')
    batch_parser.add_argument('--root', required=True, type=Path, help='game task root containing original/')
    batch_parser.add_argument('--dest-root', type=Path)
    verify_parser = commands.add_parser('verify', help='independently verify an existing candidate')
    verify_parser.add_argument('--source', required=True, type=Path)
    verify_parser.add_argument('--candidate', required=True, type=Path)
    verify_parser.add_argument('--archive', required=True, help='archive path relative to original/')
    for sub in (batch_parser, verify_parser):
        sub.add_argument('--batch-dir', type=Path)
        sub.add_argument('--report', type=Path)
        sub.add_argument('--allow-incomplete', action='store_true', help='explicitly permit missing statuses; such entries must remain byte-identical')
    args = parser.parse_args()
    if args.command == 'verify':
        batch = args.batch_dir or Path('batch_fast')
        rel = safe_rel(args.archive)
        rows = statuses(batch)
        report = verify(args.source, args.candidate, rows.get(rel, {}), rel, args.allow_incomplete)
    else:
        root = args.root.resolve()
        source_root = root / 'original'
        batch = args.batch_dir or root / 'batch_fast'
        destination_root = (args.dest_root or root / 'candidate_fast').resolve()
        if destination_root == source_root.resolve() or source_root.resolve() in destination_root.parents or destination_root in source_root.resolve().parents:
            raise ValueError('source and destination trees must be separate')
        rows = statuses(batch)
        sources = sorted(p for p in source_root.rglob('*') if p.is_file() and p.suffix.lower() == '.arc')
        if not sources:
            raise ValueError('no source archives')
        known = {p.relative_to(source_root).as_posix() for p in sources}
        if set(rows) - known:
            raise ValueError(f'status archives missing from source: {sorted(set(rows) - known)}')
        report = dict(completed=False, archives=[], errors=[])
        for source in sources:
            rel = source.relative_to(source_root).as_posix()
            try:
                report['archives'].append(repack(source, destination_root / rel, rows.get(rel, {}), batch, rel, args.allow_incomplete))
            except Exception as exc:
                report['errors'].append(dict(archive=rel, error=str(exc)))
            atomic_json(args.report or root / 'reports' / 'fast_repack.json', report)
        report['completed'] = not report['errors']
        atomic_json(args.report or root / 'reports' / 'fast_repack.json', report)
        if report['errors']:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise SystemExit(1)
    if args.report:
        atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
