"""CPU process-lifecycle controls for GPU snapshots; no CUDA or NVML calls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import resource_guard
from run_manifest import process_identity

ROOT = Path(__file__).resolve().parents[2]


class GpuIdentityTests(unittest.TestCase):
    def test_owned_unreaped_leader_retains_exact_identity_after_exit(self):
        with tempfile.TemporaryDirectory(prefix='.gpu-identity-', dir=ROOT) as tmp:
            attempt = Path(tmp)
            snapshot = dict(available=True, gpu_uuid='GPU-test', processes=[])
            guard = resource_guard.ResourceGuard(attempt, attempt,
                dict(resource_class='GPU_EXCLUSIVE', gpu_uuid='GPU-test'),
                gpu_probe=lambda _: snapshot, project_root=attempt)
            child = subprocess.Popen([sys.executable, '-c', 'import os; os.read(0, 1)'],
                                     stdin=subprocess.PIPE, start_new_session=True)
            try:
                identity = process_identity(child.pid, include_zombies=True)
                self.assertIsNotNone(identity)
                guard.child = identity
                guard.started = True
                guard.check('live-owned')
                child.stdin.close()
                os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
                self.assertIsNone(process_identity(child.pid))
                zombie = process_identity(child.pid, include_zombies=True)
                self.assertTrue(all(zombie[key] == identity[key]
                                    for key in ('pid', 'boot_id', 'start_time')))
                # NVML sampled the GPU context before teardown; the ordinary
                # /proc lookup excludes the now-unreaped leader.
                snapshot['processes'] = [dict(pid=child.pid, identity=None)]
                guard.check('sample-during-owned-exit')
                child.wait(timeout=3)
                with self.assertRaises(resource_guard.ResourceBusy):
                    guard.check('identity-no-longer-provable')
            finally:
                if child.stdin and not child.stdin.closed:
                    child.stdin.close()
                child.wait(timeout=3)

    def test_reused_pid_cannot_match_retained_owned_identity(self):
        with tempfile.TemporaryDirectory(prefix='.gpu-identity-', dir=ROOT) as tmp:
            old = dict(pid=777771, boot_id='test-boot', start_time=100,
                       ppid=1, pgrp=777771, session=777771)
            reused = dict(old, start_time=200)
            snapshot = dict(available=True, gpu_uuid='GPU-test',
                            processes=[dict(pid=old['pid'], identity=None)])
            guard = resource_guard.ResourceGuard(Path(tmp), Path(tmp),
                dict(resource_class='GPU_EXCLUSIVE', gpu_uuid='GPU-test'),
                gpu_probe=lambda _: snapshot, project_root=Path(tmp))
            guard.child = old
            guard.known_owned[old['pid']] = old
            guard.started = True
            with patch.object(resource_guard, 'owned_processes', return_value={}), \
                 patch.object(resource_guard, 'process_identity', return_value=reused):
                with self.assertRaises(resource_guard.ResourceBusy):
                    guard.check('reused-pid')


if __name__ == '__main__':
    unittest.main()
