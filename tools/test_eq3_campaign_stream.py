"""Fixed transport tests; fake producer only, never invoke a thermal solver."""
import gzip
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

from eq3_campaign_stream import replay, run_stream, verify_gzip


PRODUCER = r'''
import sys, time
layers, frames, nx, ny = map(int, sys.argv[1:5])
mode = sys.argv[5]
for z in range(layers):
    with open('field_%d.txt' % z, 'w') as f:
        f.write('%% header layer %d\n' % z)
time.sleep(.02)
for step in range(frames):
    for z in range(layers):
        with open('field_%d.txt' % z, 'a') as f:
            for row in range(ny):
                if mode == 'truncate' and step == frames-1 and z == layers-1 and row == ny-1:
                    break
                f.write(('300.000  ' * nx) + '\n')
            f.write('\n')
if mode == 'fail':
    sys.exit(7)
'''


class StreamTests(unittest.TestCase):
    def producer(self, directory, layers=3, frames=4, nx=2, ny=2, mode='ok'):
        return run_stream([sys.executable, '-c', PRODUCER, str(layers), str(frames),
                           str(nx), str(ny), mode], directory, layers, frames, nx, ny, 10)

    def test_headers_reopen_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.producer(tmp)
            self.assertEqual(result['status'], 'PASS')
            for z, field in enumerate(result['fields']):
                expected = f'% header layer {z}\n'.encode()
                expected += ((b'300.000  300.000  \n' * 2) + b'\n') * 4
                path = Path(tmp) / field['path']
                self.assertEqual(gzip.decompress(path.read_bytes()), expected)
                verify_gzip(path, hashlib.sha256(expected).hexdigest(), len(expected))
                self.assertFalse((Path(tmp) / f'field_{z}.txt').exists())

    def test_backpressure_all_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.producer(tmp, layers=63, frames=2, nx=128, ny=64)
            self.assertEqual(len(result['fields']), 63)
            self.assertTrue(all(item['frames'] == 2 for item in result['fields']))

    def test_truncation_preserves_failed_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'truncated'):
                self.producer(tmp, mode='truncate')
            self.assertTrue((Path(tmp) / 'field_0.txt').is_fifo())
            self.assertIn('FAILED', (Path(tmp) / 'stream_receipt.json').read_text())

    def test_abnormal_producer_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'producer failed'):
                self.producer(tmp, mode='fail')

    def test_bound_outer_policy_can_supply_longer_watchdog_and_process_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_stream([sys.executable,'-c',PRODUCER,'1','1','1','1','ok'],tmp,1,1,1,1,
                              watchdog=3600,process_ram_gib=.25)
            self.assertEqual(result['status'],'PASS')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'invalid dimensions or watchdog'):
                run_stream([sys.executable,'-c','pass'],tmp,1,1,1,1,watchdog=0)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'point output budget exceeded'):
                run_stream([sys.executable,'-c',PRODUCER,'1','1','1','1','ok'],tmp,1,1,1,1,watchdog=10,max_bytes=1)

    def test_replay_and_corrupt_footer(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, compressed = Path(tmp) / 'field', Path(tmp) / 'field.gz'
            source.write_bytes(b'% header\n300.000\n\n')
            result = replay(source, compressed, 1, 1, 1)
            self.assertEqual(result['uncompressed_bytes'], source.stat().st_size)
            data = bytearray(compressed.read_bytes())
            data[-8] ^= 1
            compressed.write_bytes(data)
            with self.assertRaises((OSError, EOFError)):
                verify_gzip(compressed, result['uncompressed_sha256'], result['uncompressed_bytes'])


class NativeStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library = os.environ.get('EQ3_CAMPAIGN_NATIVECODEC')
        if not cls.library:
            raise unittest.SkipTest('native codec library must be explicitly supplied')

    def producer(self, directory, mode='ok', layers=3, nx=2, ny=2):
        return run_stream([sys.executable, '-c', PRODUCER, str(layers), '4',
                           str(nx), str(ny), mode], directory, layers, 4, nx, ny, 10,
                          policy='lossless_EQ3TMK1', codec_library=self.library)

    def test_native_reopen_byte_identity(self):
        from eq3_campaign_fieldcodec import decoded_lines
        with tempfile.TemporaryDirectory() as tmp:
            result=self.producer(tmp)
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(result['output_policy'],'lossless_EQ3TMK1')
            for z,field in enumerate(result['fields']):
                original=f'% header layer {z}\n'.encode()
                original+=((b'300.000  300.000  \n'*2)+b'\n')*4
                decoded=b''.join(decoded_lines(Path(tmp)/field['path']))
                self.assertEqual(decoded,original)
                self.assertEqual(hashlib.sha256(decoded).hexdigest(),field['uncompressed_sha256'])
                self.assertEqual(field['frames'],4)
                self.assertIsNone(field['temperature_min_k'])

    def test_native_backpressure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=self.producer(tmp,layers=63,nx=128,ny=64)
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(len(result['fields']),63)

    def test_native_truncation_retains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.producer(tmp,mode='truncate')
            self.assertTrue((Path(tmp)/'field_0.txt').is_fifo())
            self.assertTrue((Path(tmp)/'field_0.txt.tmk').is_file())
            self.assertIn('FAILED',(Path(tmp)/'stream_receipt.json').read_text())

    def test_native_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'field_0.txt.tmk'
            target.write_bytes(b'original')
            with self.assertRaises(FileExistsError):
                self.producer(tmp)
            self.assertEqual(target.read_bytes(),b'original')


if __name__ == '__main__':
    unittest.main()
