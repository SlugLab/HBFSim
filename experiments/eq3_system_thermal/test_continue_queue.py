import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from continue_queue import run


class QueueTests(unittest.TestCase):
    def fixture(self, root, commands):
        before = root / 'predecessor'; before.mkdir()
        (before / 'DONE.json').write_text('{"status":"COMPLETED"}')
        frozen = root / 'frozen'; frozen.write_text('input')
        manifest = root / 'QUEUE.json'
        manifest.write_text(json.dumps({'wait_for_stage': str(before),
            'wait_timeout_s': 1, 'check_interval_s': .01, 'source_root': str(root),
            'locks': {str(frozen): hashlib.sha256(b'input').hexdigest()},
            'phases': [{'id': str(i), 'command': [sys.executable, '-c', command]}
                       for i, command in enumerate(commands)]}))
        return manifest

    def test_independent_phase_continues_after_recorded_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run(self.fixture(root, ['raise SystemExit(2)', 'open("next", "w").write("ran")']))
            result = json.loads((root / 'QUEUE_DONE.json').read_text())
            self.assertEqual([r['exit_code'] for r in result['results']], [2, 0])
            self.assertEqual(result['status'], 'FINISHED_WITH_FAILURES')
            self.assertTrue((root / 'next').exists())
            self.assertEqual(result['ai_wakeup'], 'UNAVAILABLE')

    def test_changed_frozen_input_cannot_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); manifest = self.fixture(root, ['open("bad", "w").close()'])
            (root / 'frozen').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'frozen queue input changed'):
                run(manifest)
            self.assertFalse((root / 'bad').exists())

    def test_queue_cannot_replay_completed_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); manifest = self.fixture(root, [])
            run(manifest)
            with self.assertRaises(FileExistsError):
                run(manifest)


if __name__ == '__main__':
    unittest.main()
