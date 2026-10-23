"""Synthetic tests only: python -m unittest discover -s tools -p test_repack_fast.py -v"""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import repack_fast as tool


class RepackSmoke(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.fast-smoke-', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'original.arc'
        self.candidate = self.root / 'candidate.arc'
        self.batch = self.root / 'batch_fast'
        self.rel = 'Archive/tachie.arc'
        meta = struct.pack('<8H', 32, 16, 32, 0, 4, 9, 0x1234, 0xabcd)
        self.cbg = tool.CBG + meta + b'original compressed data' * 20
        self.envelope = tool.CBG + meta + b'JXLCBG01' + struct.pack('<I', 8) + b'\0\0XJ' + b'fake-jxl'
        self.retained = b'unchanged audio'
        self.leading, self.gap, self.trailer = b'LEADING\x00', b'GAP\xff', b'TRAILER\x80'
        first = len(self.leading)
        second = first + len(self.retained) + len(self.gap)
        # Index order differs from physical order, with zero-length anchors at boundaries.
        specs = [(b'tachie', second, self.cbg), (b'audio', first, self.retained),
                 (b'empty-start', first, b''), (b'empty-end', second + len(self.cbg), b'')]
        records = []
        for i, (name, offset, payload) in enumerate(specs):
            rec = bytearray(bytes([0x70 + i]) * 128)
            rec[:96] = name + b'\0' + bytes([0xa0 + i]) * (95 - len(name))
            struct.pack_into('<II', rec, 96, offset, len(payload))
            records.append(rec)
        self.source.write_bytes(tool.MAGIC + struct.pack('<I', len(specs)) + b''.join(records) +
                                self.leading + self.retained + self.gap + self.cbg + self.trailer)
        out = self.batch / 'output' / self.rel / '0.cbgjxl'
        out.parent.mkdir(parents=True)
        out.write_bytes(self.envelope)
        self.row = dict(archive=self.rel, index=0, name='tachie', status='converted',
                        source_sha256=hashlib.sha256(self.cbg).hexdigest(),
                        output_sha256=hashlib.sha256(self.envelope).hexdigest(),
                        original_bytes=len(self.cbg), new_bytes=len(self.envelope),
                        policy_version=tool.POLICY, validation_schema=tool.SCHEMA,
                        quality=90, effort=7, threads=2)
        self.selected = {0: self.row}

    def build(self):
        return tool.repack(self.source, self.candidate, self.selected, self.batch, self.rel)

    def test_repack_preserves_all_regions_records_and_physical_order(self):
        report = self.build()
        self.assertTrue(report['verified'])
        self.assertEqual([x['status'] for x in report['entries']], ['converted', 'unchanged', 'unchanged', 'unchanged'])
        self.assertEqual([x['length'] for x in report['opaque_regions']], [len(self.leading), len(self.gap), len(self.trailer)])
        _, old, base, _ = tool.parse(self.source)
        _, new, _, _ = tool.parse(self.candidate)
        self.assertEqual(self.candidate.read_bytes()[base:], self.leading + self.retained + self.gap + self.envelope + self.trailer)
        for a, b in zip(old, new):
            self.assertEqual(a.record[:96] + a.record[104:], b.record[:96] + b.record[104:])
        self.assertEqual(new[2].offset, new[1].offset)
        self.assertEqual(new[3].offset, new[0].offset + new[0].length)

    def test_gap_trailer_payload_record_and_header_corruption_rejected(self):
        report = self.build()
        pristine = self.candidate.read_bytes()
        _, entries, base, _ = tool.parse(self.candidate)
        locations = [0, 16 + 104, base + entries[1].offset,
                     base + report['opaque_regions'][0]['output_offset'],
                     base + report['opaque_regions'][1]['output_offset'], len(pristine) - 1]
        for position in locations:
            with self.subTest(position=position):
                data = bytearray(pristine)
                data[position] ^= 1
                self.candidate.write_bytes(data)
                with self.assertRaises(ValueError):
                    tool.verify(self.source, self.candidate, self.selected, self.rel)

    def test_metadata_bad_marker_length_and_no_benefit_rejected(self):
        for position in (0, 16, 22, 31, 32, 40, 44, 47):
            with self.subTest(position=position):
                bad = bytearray(self.envelope)
                bad[position] ^= 1
                with self.assertRaises(ValueError):
                    tool.envelope(self.cbg[:48], len(self.cbg), bad[:48], len(bad))
        with self.assertRaises(ValueError):
            tool.envelope(self.cbg[:48], len(self.envelope), self.envelope[:48], len(self.envelope))

    def test_failed_repack_keeps_existing_destination_and_cleans_temporary(self):
        self.candidate.write_bytes(b'existing candidate')
        self.row['output_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(self.candidate.read_bytes(), b'existing candidate')
        self.assertFalse(list(self.root.glob('*.partial')))

    def test_verify_failure_before_publish_is_atomic(self):
        from unittest.mock import patch
        self.candidate.write_bytes(b'existing candidate')
        with patch.object(tool, 'verify', side_effect=ValueError('synthetic verification failure')):
            with self.assertRaises(ValueError):
                self.build()
        self.assertEqual(self.candidate.read_bytes(), b'existing candidate')
        self.assertFalse(list(self.root.glob('*.partial')))

    def test_missing_cbg_status_requires_explicit_incomplete(self):
        with self.assertRaises(ValueError):
            tool.repack(self.source, self.candidate, {}, self.batch, self.rel)
        tool.repack(self.source, self.candidate, {}, self.batch, self.rel, True)
        self.assertEqual(self.source.read_bytes(), self.candidate.read_bytes())

    def test_bounds_overlap_and_zero_inside_replacement_rejected(self):
        pristine = self.source.read_bytes()
        for off, length in ((0xffffffff, 99), (0, 0xffffffff), (len(self.leading), len(self.cbg))):
            with self.subTest(offset=off, length=length):
                data = bytearray(pristine)
                struct.pack_into('<II', data, 16 + 96, off, length)
                self.source.write_bytes(data)
                with self.assertRaises(ValueError):
                    tool.parse(self.source)
        self.source.write_bytes(pristine)
        _, entries, _, size = tool.parse(self.source)
        entries[2].offset = entries[0].offset + 1
        with self.assertRaises(ValueError):
            tool.layout(entries, {e.index: len(self.envelope) if e.index == 0 else e.length for e in entries}, size - 16 - 128 * 4)

    def test_status_errors_duplicates_paths_and_hash_validation(self):
        status_dir = self.batch / 'status' / self.rel
        status_dir.mkdir(parents=True)
        file = status_dir / '0.json'
        file.write_text(json.dumps(self.row), encoding='utf-8')
        self.assertEqual(tool.statuses(self.batch)[self.rel][0]['index'], 0)
        for key, value in (('status', 'error'), ('archive', '../bad.arc'), ('archive', 'C:/bad.arc'),
                           ('source_sha256', 'xyz'), ('policy_version', 'old')):
            with self.subTest(key=key, value=value):
                row = dict(self.row, **{key: value})
                file.write_text(json.dumps(row), encoding='utf-8')
                with self.assertRaises(ValueError):
                    tool.statuses(self.batch)
        file.write_text(json.dumps(self.row), encoding='utf-8')
        (status_dir / 'duplicate.json').write_text(json.dumps(self.row), encoding='utf-8')
        with self.assertRaises(ValueError):
            tool.statuses(self.batch)

    def test_retained_status(self):
        self.selected[0] = dict(self.row, status='retained', new_bytes=len(self.cbg), output_sha256=self.row['source_sha256'])
        self.build()
        self.assertEqual(self.source.read_bytes(), self.candidate.read_bytes())

    def test_allowlisted_evb_opaque_stream_copy_and_verification(self):
        data = b'EVB\0@\0\0\0' + bytes(range(56)) + b'opaque translation bytes' * 100
        self.source.write_bytes(data)
        report = tool.repack(self.source, self.candidate, {}, self.batch, self.rel)
        self.assertEqual(report['kind'], 'opaque')
        self.assertEqual(report['source_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.candidate.read_bytes(), data)
        self.assertTrue(tool.verify(self.source, self.candidate, {}, self.rel)['verified'])
        self.candidate.write_bytes(data[:-1] + b'!')
        with self.assertRaises(ValueError):
            tool.verify(self.source, self.candidate, {}, self.rel)
        with self.assertRaises(ValueError):
            tool.repack(self.source, self.candidate, self.selected, self.batch, self.rel)

    def test_foreign_allowlist_never_hides_malformed_arc20(self):
        bad_headers = [b'EVB\0@\0\0\0' + b'x' * 8,
                       b'EVB\0A\0\0\0' + b'x' * 100,
                       b'UNKNOWN FORMAT!!' + b'x' * 100,
                       tool.MAGIC,
                       tool.MAGIC + struct.pack('<I', 999) + b'x' * 100]
        for data in bad_headers:
            with self.subTest(header=data[:16]):
                self.source.write_bytes(data)
                self.candidate.write_bytes(b'previous candidate')
                with self.assertRaises(ValueError):
                    tool.repack(self.source, self.candidate, {}, self.batch, self.rel)
                self.assertEqual(self.candidate.read_bytes(), b'previous candidate')


if __name__ == '__main__':
    unittest.main()
