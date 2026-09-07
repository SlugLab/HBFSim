"""CPU-only tests of lossless bounded raw observation storage."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import gpu_delay_raw as raw


class RawStorageTests(unittest.TestCase):
    def test_roundtrip_preserves_exact_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);data=b'{"observation": [1, 2, 3], "text":"exact formatting"}\n'
            (p/'raw.json').write_bytes(data)
            self.assertEqual(raw.read_raw(p),data)
            receipt=raw.compress_raw(p)
            self.assertFalse((p/'raw.json').exists())
            self.assertEqual(raw.read_raw(p),data)
            self.assertEqual(receipt['uncompressed_bytes'],len(data))
            with self.assertRaises((ValueError,OSError)):raw.compress_raw(p)

    def test_modified_compressed_payload_or_sidecar_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'raw.json').write_text('{"value":17}')
            raw.compress_raw(p)
            sidecar=p/'raw.json.gzip.json';record=json.loads(sidecar.read_text())
            record['uncompressed_bytes']+=1;sidecar.write_text(json.dumps(record))
            with self.assertRaises(ValueError):raw.read_raw(p)

    def test_input_limit_and_ambiguous_sources_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'raw.json').write_bytes(b'x'*100)
            with self.assertRaises(ValueError):raw.compress_raw(p,max_bytes=99)
            self.assertEqual((p/'raw.json').stat().st_size,100)
            raw.compress_raw(p)
            with self.assertRaises(ValueError):raw.read_raw(p,max_bytes=99)
            (p/'raw.json').write_text('ambiguous')
            with self.assertRaises(ValueError):raw.read_raw(p)

    def test_fifo_and_symlink_never_open_as_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);os.mkfifo(p/'raw.json')
            with self.assertRaises(ValueError):raw.read_raw(p)
            (p/'raw.json').unlink();(p/'target').write_text('{}');(p/'raw.json').symlink_to(p/'target')
            with self.assertRaises((ValueError,OSError)):raw.read_raw(p)

    def test_missing_sidecar_and_unbounded_request_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'raw.json').write_text('{}');raw.compress_raw(p)
            (p/'raw.json.gzip.json').unlink()
            with self.assertRaises((ValueError,OSError)):raw.read_raw(p)
            with self.assertRaises(ValueError):raw.read_raw(p,max_bytes=raw.MAX_RAW_BYTES+1)

    def test_completed_attempt_cannot_be_compressed_retroactively(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt=Path(tmp);arm=attempt/'raw.triplet/native';arm.mkdir(parents=True)
            data=b'{"original":"sealed"}';(arm/'raw.json').write_bytes(data)
            (attempt/'status.json').write_text('{"state":"DONE"}')
            with self.assertRaisesRegex(ValueError,'completed'):
                raw.compress_raw(arm)
            self.assertEqual((arm/'raw.json').read_bytes(),data)
            self.assertFalse((arm/'raw.json.gz').exists())

    def test_pre_unlink_directory_fsync_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);data=b'{"original":"durable"}';(p/'raw.json').write_bytes(data)
            fsync=os.fsync;calls=[]
            def fail_third(fd):
                calls.append(fd)
                if len(calls)==3:raise OSError('pre-unlink directory fsync failed')
                fsync(fd)
            with mock.patch.object(raw.os,'fsync',side_effect=fail_third):
                with self.assertRaises(OSError):raw.compress_raw(p)
            self.assertEqual((p/'raw.json').read_bytes(),data)
            self.assertFalse((p/'raw.json.gz').exists())
            self.assertFalse((p/'raw.json.gzip.json').exists())

    def test_final_directory_fsync_failure_retains_verified_compressed_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);data=b'{"original":"durable"}';(p/'raw.json').write_bytes(data)
            fsync=os.fsync;calls=[]
            def fail_fourth(fd):
                calls.append(fd)
                if len(calls)==4:raise OSError('final directory fsync failed')
                fsync(fd)
            with mock.patch.object(raw.os,'fsync',side_effect=fail_fourth):
                with self.assertRaises(OSError):raw.compress_raw(p)
            self.assertFalse((p/'raw.json').exists())
            self.assertEqual(raw.read_raw(p),data)

    def test_compressed_payload_byte_flip_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'raw.json').write_bytes(b'{"original":"durable"}');raw.compress_raw(p)
            data=bytearray((p/'raw.json.gz').read_bytes());data[len(data)//2]^=1
            (p/'raw.json.gz').write_bytes(data)
            with self.assertRaisesRegex(ValueError,'compressed raw hash'):
                raw.read_raw(p)


if __name__=='__main__':unittest.main()
