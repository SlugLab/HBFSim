"""Fixed Python/native EQ3TMK1 compatibility tests; no numerical solver."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from eq3_campaign_fieldcodec import Encoder as PythonEncoder
from eq3_campaign_fieldcodec import decoded_lines as python_decoded_lines
from eq3_campaign_fieldcodec import native_value
from eq3_campaign_nativecodec import Encoder as NativeEncoder
from eq3_campaign_nativecodec import decoded_lines as native_decoded_lines
from eq3_campaign_nativecodec import replay as native_replay


LIBRARY = os.environ.get("EQ3_CAMPAIGN_NATIVECODEC")


def fixture():
    value = b"% native header exact\n\n"
    for frame in range(4):
        for row in range(2):
            value += b"".join(native_value(item + frame) for item in
                                (300001 + row, 399999 - row, -1001)) + b"\n"
        value += b"\n"
    return value


@unittest.skipUnless(LIBRARY, "EQ3_CAMPAIGN_NATIVECODEC is required")
class NativeCodecCompatibilityTests(unittest.TestCase):
    def test_python_encoding_native_decoding_is_byte_exact(self):
        native = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "python.mkz"
            encoder = PythonEncoder(target, 3, 2, 4)
            for offset in range(0, len(native), 7):
                encoder.feed(native[offset:offset + 7])
            receipt = encoder.finish()
            decoded = b"".join(native_decoded_lines(target, library=LIBRARY))
            self.assertEqual(native, decoded)
            self.assertEqual(receipt["native_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_native_encoding_python_decoding_is_byte_exact(self):
        native = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "native.mkz"
            encoder = NativeEncoder(target, 3, 2, 4, library=LIBRARY)
            try:
                for offset in range(0, len(native), 5):
                    encoder.feed(native[offset:offset + 5])
                receipt = encoder.finish()
            finally:
                encoder.close()
            decoded = b"".join(python_decoded_lines(target))
            self.assertEqual(native, decoded)
            self.assertEqual(receipt["native_sha256"], hashlib.sha256(decoded).hexdigest())
            self.assertEqual(8, receipt["rows"])

    def test_native_replay_verifies_full_hash_frames_and_original_bytes(self):
        native = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "native.txt"
            target = Path(tmp) / "native.mkz"
            source.write_bytes(native)
            receipt = native_replay(
                source, target, 3, 2, 4, library=LIBRARY
            )
            self.assertTrue(receipt["decoded_sha256_verified"])
            self.assertTrue(receipt["decoded_rows_verified"])
            self.assertEqual(native, b"".join(native_decoded_lines(
                target, library=LIBRARY
            )))

    def test_native_encoder_rejects_noncanonical_rows(self):
        for row in (b"300.000\n", b"300.0001  \n", b"nan  \n", b" -0.000  \n"):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as tmp:
                encoder = NativeEncoder(
                    Path(tmp) / "bad.mkz", 1, 1, 1, library=LIBRARY
                )
                try:
                    with self.assertRaises(ValueError):
                        encoder.feed(row)
                finally:
                    encoder.close()

    def test_native_decoder_rejects_truncation_corruption_and_trailing_bytes(self):
        native = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "python.mkz"
            encoder = PythonEncoder(target, 3, 2, 4)
            encoder.feed(native)
            encoder.finish()
            original = target.read_bytes()
            corrupt = bytearray(original)
            corrupt[-1] ^= 1
            for payload in (original[:-1], bytes(corrupt), original + b"extra"):
                target.write_bytes(payload)
                with self.assertRaises(ValueError):
                    list(native_decoded_lines(target, library=LIBRARY))


if __name__ == "__main__":
    unittest.main()
