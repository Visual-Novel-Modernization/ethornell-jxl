"""Stream-repack ARC20 archives from validated batch status records.

Reads batch/status/*.json and batch/output/<archive_stem>/<index>.cbgjxl.
Writes candidate/Archive atomically; original and test trees are read-only.
"""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, struct, tempfile
from dataclasses import dataclass

ROOT = pathlib.Path(os.environ.get("BGI_GAME_DIR", "."))
MAGIC = b"BURIKO ARC20"
HEADER_SIZE = 16
RECORD_SIZE = 128
COPY_CHUNK = 8 * 1024 * 1024

@dataclass
class Entry:
    index: int
    record: bytes
    offset: int
    length: int
    expected: dict | None = None
    replacement: pathlib.Path | None = None
    out_offset: int = 0
    out_length: int = 0


def sha_range(f, start: int, length: int) -> str:
    h = hashlib.sha256()
    f.seek(start)
    left = length
    while left:
        b = f.read(min(COPY_CHUNK, left))
        if not b:
            raise IOError("unexpected EOF while hashing range")
        h.update(b)
        left -= len(b)
    return h.hexdigest()


def sha_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(COPY_CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def copy_range(src, dst, start: int, length: int, hasher=None):
    src.seek(start)
    left = length
    while left:
        b = src.read(min(COPY_CHUNK, left))
        if not b:
            raise IOError("unexpected EOF while copying range")
        dst.write(b)
        if hasher is not None:
            hasher.update(b)
        left -= len(b)


def load_statuses(status_dir: pathlib.Path):
    by_archive: dict[str, dict[int, dict]] = {}
    if not status_dir.exists():
        return by_archive
    for path in sorted(status_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = data if isinstance(data, list) else data.get("records", data.get("entries", [data]))
        if not isinstance(rows, list):
            raise ValueError(f"status file must contain a record list: {path}")
        for row in rows:
            required = ("archive", "index", "name", "source_sha256", "output_sha256",
                        "original_bytes", "new_bytes", "status", "policy_version", "validation_schema")
            missing = [k for k in required if k not in row]
            if missing:
                raise ValueError(f"{path}: missing status fields {missing}")
            status = row["status"]
            if status not in ("converted", "retained"):
                raise ValueError(f"{path}: invalid status {status!r}")
            if row.get('policy_version') != 'opaque-type1-v1' or row.get('validation_schema') != 'jxl-cbg-v2':
                raise ValueError(f"{path}: status was produced by an incompatible conversion policy")
            if status == 'converted' and (not row.get('decode_verified') or float(row.get('decoder_max_channel_difference',99)) > 3 or float(row.get('decoder_rmse',99)) > 1):
                raise ValueError(f"{path}: converted output lacks current decoder validation")
            archive = pathlib.PurePosixPath(str(row["archive"]).replace("\\", "/")).as_posix()
            if archive.startswith("../") or archive.startswith("/"):
                raise ValueError(f"unsafe archive path in {path}: {archive}")
            idx = int(row["index"])
            slot = by_archive.setdefault(archive, {})
            if idx in slot:
                raise ValueError(f"duplicate status for {archive} index {idx}")
            row = dict(row)
            row["archive"] = archive
            row["index"] = idx
            slot[idx] = row
    return by_archive


def parse_archive(path: pathlib.Path):
    size = path.stat().st_size
    with path.open("rb") as f:
        header = f.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE or header[:12] != MAGIC:
            raise ValueError(f"not a BURIKO ARC20 archive: {path}")
        count = struct.unpack_from("<I", header, 12)[0]
        index_end = HEADER_SIZE + count * RECORD_SIZE
        if index_end > size:
            raise ValueError(f"ARC20 index outside file: {path}")
        records = []
        for i in range(count):
            f.seek(HEADER_SIZE + i * RECORD_SIZE)
            rec = f.read(RECORD_SIZE)
            if len(rec) != RECORD_SIZE:
                raise ValueError(f"short index record {i}: {path}")
            off, length = struct.unpack_from("<II", rec, 96)
            start = index_end + off
            end = start + length
            if start < index_end or end > size:
                raise ValueError(f"entry {i} out of bounds: {path}")
            records.append(Entry(i, rec, off, length))
    positive = sorted((e.offset, e.offset + e.length, e.index) for e in records if e.length)
    overlaps = []
    cursor = 0
    for start, end, idx in positive:
        if start < cursor:
            overlaps.append((idx, start, cursor))
        cursor = max(cursor, end)
    if overlaps:
        raise ValueError(f"overlapping ARC20 payloads in {path}: {overlaps[:5]}")
    data_end = max((e.offset + e.length for e in records), default=0)
    data_size = size - index_end
    if data_end > data_size:
        raise ValueError(f"payload extent outside data area: {path}")
    return header, records, index_end, size, data_end


def entry_name(e: Entry):
    return e.record[:96].split(b"\0", 1)[0].decode("cp932", errors="replace")


def apply_statuses(archive_rel: str, records: list[Entry], statuses, output_dir: pathlib.Path):
    selected = statuses.get(archive_rel, {})
    stem = pathlib.PurePosixPath(archive_rel).stem
    known = {e.index for e in records}
    unknown = set(selected) - known
    if unknown:
        raise ValueError(f"status index out of range for {archive_rel}: {sorted(unknown)[:8]}")
    for e in records:
        row = selected.get(e.index)
        if row is None:
            continue
        e.expected = row
        if row["name"] != entry_name(e):
            raise ValueError(f"name mismatch: {archive_rel} index {e.index}: {row['name']!r} != {entry_name(e)!r}")
        if int(row["original_bytes"]) != e.length:
            raise ValueError(f"original_bytes mismatch: {archive_rel} index {e.index}")
        if row["status"] == "converted":
            candidate = output_dir / stem / f"{e.index}.cbgjxl"
            if not candidate.is_file():
                raise FileNotFoundError(f"converted status lacks output: {candidate}")
            if candidate.stat().st_size != int(row["new_bytes"]):
                raise ValueError(f"new_bytes mismatch: {candidate}")
            digest = sha_file(candidate)
            if digest.lower() != str(row["output_sha256"]).lower():
                raise ValueError(f"output_sha256 mismatch: {candidate}")
            e.replacement = candidate
            e.out_length = candidate.stat().st_size
        else:
            if int(row["new_bytes"]) != e.length:
                raise ValueError(f"retained entry new_bytes must equal source length: {archive_rel} index {e.index}")
            if str(row["output_sha256"]).lower() != str(row["source_sha256"]).lower():
                raise ValueError(f"retained entry output/source hash mismatch: {archive_rel} index {e.index}")
            e.out_length = e.length
    for e in records:
        if e.replacement is None:
            e.out_length = e.length
    return selected


def layout_by_physical(records: list[Entry], data_size: int):
    """Assign output offsets while preserving gaps and trailer as opaque byte regions."""
    ordered = sorted(records, key=lambda e: (e.offset, e.index))
    cursor_in = 0
    cursor_out = 0
    for e in ordered:
        if e.length == 0:
            if e.offset < cursor_in:
                raise ValueError(f"zero-length entry lies inside another payload: index {e.index}")
            cursor_out += e.offset - cursor_in
            cursor_in = e.offset
            e.out_offset = cursor_out
            continue
        if e.offset < cursor_in:
            raise ValueError(f"overlap encountered at index {e.index}")
        cursor_out += e.offset - cursor_in
        cursor_in = e.offset
        e.out_offset = cursor_out
        cursor_out += e.out_length
        cursor_in += e.length
    if cursor_in > data_size:
        raise ValueError("payloads exceed source data area")
    trailer_len = data_size - cursor_in
    internal_gap = sum(max(0, b.offset - (a.offset + a.length)) for a, b in zip(ordered, ordered[1:]))
    return ordered, cursor_out, trailer_len, internal_gap


def repack_one(source: pathlib.Path, destination: pathlib.Path, archive_rel: str,
               statuses, output_dir: pathlib.Path):
    header, records, index_end, source_size, data_end = parse_archive(source)
    selected = apply_statuses(archive_rel, records, statuses, output_dir)
    data_size = source_size - index_end
    ordered, payload_out_end, trailer_len, internal_gap = layout_by_physical(records, data_size)
    # If archive has a trailer after the maximum payload, preserve it byte-for-byte.
    expected_size = index_end + payload_out_end + trailer_len
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    tmp = pathlib.Path(tmpname)
    rows = []
    try:
        with source.open("rb") as src, tmp.open("wb") as dst:
            for e in records:
                if e.expected and sha_range(src,index_end+e.offset,e.length) != e.expected['source_sha256'].lower():
                    raise ValueError(f'Source payload hash changed: {archive_rel} index {e.index}')
                if e.replacement is not None and not e.expected.get('decode_verified', False):
                    raise ValueError(f'Replacement lacks decode verification: {archive_rel} index {e.index}')
            dst.write(header)
            for e in records:
                rec = bytearray(e.record)
                struct.pack_into("<II", rec, 96, e.out_offset, e.out_length)
                dst.write(rec)
            src_cursor = 0
            for e in ordered:
                if e.length == 0:
                    continue
                if e.offset > src_cursor:
                    copy_range(src, dst, index_end + src_cursor, e.offset - src_cursor)
                if e.replacement is None:
                    h = hashlib.sha256()
                    copy_range(src, dst, index_end + e.offset, e.length, h)
                    actual = h.hexdigest()
                    if e.expected and actual.lower() != str(e.expected["source_sha256"]).lower():
                        raise ValueError(f"source_sha256 mismatch: {archive_rel} index {e.index}")
                    expected_hash = actual
                    status = e.expected["status"] if e.expected else "unchanged"
                else:
                    with e.replacement.open("rb") as repl:
                        shutil_copy_stream(repl, dst)
                    expected_hash = str(e.expected["output_sha256"]).lower()
                    status = "converted"
                rows.append({"index":e.index,"name":entry_name(e),"status":status,
                             "source_sha256":e.expected["source_sha256"] if e.expected else None,
                             "output_sha256":expected_hash,"original_bytes":e.length,"new_bytes":e.out_length})
                src_cursor = e.offset + e.length
            if src_cursor < data_size:
                copy_range(src, dst, index_end + src_cursor, data_size - src_cursor)
            dst.flush()
            os.fsync(dst.fileno())
        if tmp.stat().st_size != expected_size:
            raise ValueError(f"repacked size mismatch: got {tmp.stat().st_size}, expected {expected_size}")
        verify_archive(tmp, source, records, index_end, rows, selected)
        os.replace(tmp, destination)
    except Exception:
        try: tmp.unlink()
        except FileNotFoundError: pass
        raise
    return {"archive":archive_rel,"source_bytes":source_size,"output_bytes":destination.stat().st_size,"source_sha256":sha_file(source),"output_sha256":sha_file(destination),
            "entry_count":len(records),"converted_count":sum(r["status"]=="converted" for r in rows),
            "retained_count":sum(r["status"]=="retained" for r in rows),
            "unchanged_count":sum(r["status"]=="unchanged" for r in rows),
            "data_gap_bytes":internal_gap,"trailer_bytes":trailer_len,
            "entries":rows,"verified":True,"output":str(destination)}


def shutil_copy_stream(src, dst):
    while True:
        b = src.read(COPY_CHUNK)
        if not b: break
        dst.write(b)


def verify_archive(candidate: pathlib.Path, source: pathlib.Path, original_records: list[Entry],
                   index_end: int, rows, selected):
    h, reread, _, size, _ = parse_archive(candidate)
    if len(reread) != len(original_records):
        raise ValueError("entry count changed")
    for old, new in zip(original_records, reread):
        if old.record[:96] != new.record[:96] or old.record[104:] != new.record[104:]:
            raise ValueError(f"index immutable fields changed at {old.index}")
        if old.out_offset != new.offset or old.out_length != new.length:
            raise ValueError(f"index offset/length mismatch at {old.index}")
    out_base = HEADER_SIZE + len(reread) * RECORD_SIZE
    report_by_idx = {r["index"]:r for r in rows}
    with source.open("rb") as sf, candidate.open("rb") as cf:
        for old, new in zip(original_records, reread):
            if old.replacement is not None:
                expected = str(old.expected["output_sha256"]).lower()
            else:
                expected = str(old.expected["source_sha256"]).lower() if old.expected else sha_range(sf,index_end+old.offset,old.length)
            actual = sha_range(cf,out_base+new.offset,new.length)
            if actual.lower() != expected.lower():
                raise ValueError(f"payload SHA256 mismatch at index {old.index}: {actual} != {expected}")
    if size < out_base:
        raise ValueError("output truncated before data")


def run(root: pathlib.Path, dest_root: pathlib.Path | None = None):
    source_root = root / "original"
    status_dir = root / "batch" / "status"
    output_dir = root / "batch" / "output"
    candidate_root = dest_root if dest_root is not None else (root / "candidate")
    report_path = root / "reports" / "batch_repack.json"
    statuses = load_statuses(status_dir)
    report = {"source_root":str(source_root),"candidate_root":str(candidate_root),"status_dir":str(status_dir),"archives":[],"status_archives":sorted(statuses)}
    for source in sorted(source_root.rglob("*.arc")):
        rel = source.relative_to(source_root).as_posix()
        if rel not in statuses:
            continue
        dest = candidate_root / pathlib.PurePosixPath(rel)
        row = repack_one(source,dest,rel,statuses,output_dir)
        report["archives"].append(row)
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    missing = set(statuses)-{r["archive"] for r in report["archives"]}
    if missing:
        raise FileNotFoundError(f"status refers to absent source archives: {sorted(missing)}")
    report["completed"] = True
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def self_test(root: pathlib.Path):
    """Create a synthetic title-like ARC20 with gaps/trailer only in scratch."""
    scratch = root / "scratch" / "repack_batch_selftest"
    if scratch.exists():
        import shutil
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    src = scratch / "title_fixture.arc"
    dst = scratch / "candidate.arc"
    output_dir = scratch / "batch" / "output" / "title_fixture"
    status_dir = scratch / "batch" / "status"
    output_dir.mkdir(parents=True); status_dir.mkdir(parents=True)
    # Two entries physically ordered opposite the index, with explicit gap and trailer.
    count=2; index_end=16+count*RECORD_SIZE
    payload0=b"JXL-TITLE-ENVELOPE"; payload1=b"retained-menu-payload"
    gap=b"GAP!\0\x7f"; trailer=b"TRAILER-OPAQUE"
    records=[]
    for name,off,payload,opaque in ((b"title_all",len(payload1)+len(gap),payload0,bytes(range(24))),
                                    (b"menu_base",0,payload1,bytes(reversed(range(24))))):
        r=bytearray(RECORD_SIZE);r[:len(name)]=name;struct.pack_into("<II",r,96,off,len(payload));r[104:128]=opaque;records.append(bytes(r))
    src.write_bytes(MAGIC+struct.pack("<I",count)+b"".join(records)+payload1+gap+payload0+trailer)
    replacement=b"JXL-TITLE-ENVELOPE-UPDATED"
    repl=output_dir/"0.cbgjxl";repl.write_bytes(replacement)
    status={"archive":"title_fixture.arc","index":0,"name":"title_all",
      "source_sha256":hashlib.sha256(payload0).hexdigest(),"output_sha256":hashlib.sha256(replacement).hexdigest(),
      "original_bytes":len(payload0),"new_bytes":len(replacement),"status":"converted",
       "decode_verified":True,"decoder_max_channel_difference":0,"decoder_rmse":0,
       "policy_version":"opaque-type1-v1","validation_schema":"jxl-cbg-v2"}
    (status_dir/"title.json").write_text(json.dumps([status]),encoding="utf-8")
    statuses=load_statuses(status_dir)
    # Adapt output root: helper expects batch/output/<stem> and archive stem.
    result=repack_one(src,dst,"title_fixture.arc",statuses,output_dir.parent)
    h,rs,base,size,data_end=parse_archive(dst)
    assert rs[0].record[:96]==records[0][:96] and rs[0].record[104:]==records[0][104:]
    assert rs[1].record[:96]==records[1][:96] and rs[1].record[104:]==records[1][104:]
    # Original opaque gap and trailer bytes must remain in order and unchanged.
    rebuilt=dst.read_bytes()[base:]
    assert gap in rebuilt and trailer in rebuilt
    assert result["data_gap_bytes"]==len(gap)
    assert result["trailer_bytes"]==len(trailer)
    return {"scratch":str(scratch),"converted_payload_verified":True,"retained_payload_verified":True,
            "name_and_opaque_fields_preserved":True,"gap_bytes":len(gap),"trailer_bytes":len(trailer),"atomic_output":True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path, default=ROOT)
    ap.add_argument("--dest-root", type=pathlib.Path, default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        print(json.dumps(self_test(a.root), ensure_ascii=False, indent=2))
        return
    print(json.dumps(run(a.root, a.dest_root), ensure_ascii=False, indent=2))
if __name__=="__main__":main()
