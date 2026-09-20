"""Fixed byte-lossless codec tests; no numerical solver."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zlib

from eq3_campaign_fieldcodec import Encoder, decoded_lines, native_value, replay


class CodecTests(unittest.TestCase):
    def test_chunk_boundaries_frame_deltas_and_literal_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'field.mkz'
            native = b'% native header exact\n\n'
            for frame in range(4):
                for row in range(2):
                    native += b''.join(native_value(v+frame) for v in
                                       [300001+row, 399999-row, -1001]) + b'\n'
                native += b'\n'
            encoder = Encoder(path, 3, 2, 4)
            for offset in range(0,len(native),7):
                encoder.feed(native[offset:offset+7])
            receipt = encoder.finish()
            decoded = b''.join(decoded_lines(path))
            self.assertEqual(decoded, native)
            self.assertEqual(receipt['native_sha256'], hashlib.sha256(decoded).hexdigest())

    def test_reject_noncanonical_or_precision_loss(self):
        for row in (b'300.000\n', b'300.0001  \n', b'nan  \n', b' -0.000  \n'):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as tmp:
                encoder = Encoder(Path(tmp)/'field.mkz',1,1,1)
                try:
                    with self.assertRaises(ValueError):
                        encoder.feed(row)
                finally:
                    encoder.file.close()

    def test_truncated_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            encoder = Encoder(Path(tmp)/'field.mkz',1,1,2)
            try:
                encoder.feed(b'300.000  \n')
                with self.assertRaisesRegex(ValueError,'truncated'):
                    encoder.finish()
            finally:
                encoder.file.close()

    def test_corrupt_truncated_and_extra_compressed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'raw', Path(tmp)/'field.mkz'
            source.write_bytes(b'% header\n300.000  \n\n')
            replay(source,target,1,1,1)
            original = target.read_bytes()
            bad = bytearray(original); bad[-1] ^= 1
            for data in (original[:-1], bytes(bad), original+b'extra'):
                target.write_bytes(data)
                with self.assertRaises((ValueError,zlib.error)):
                    list(decoded_lines(target))


if __name__ == '__main__':
    unittest.main()
