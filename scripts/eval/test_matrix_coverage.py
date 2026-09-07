"""Coverage is an evidence ledger, not a second scheduler state machine."""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('matrix_coverage'), 'coverage exporter missing')
        import matrix_coverage
        self.module = matrix_coverage
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results = pathlib.Path(self.tmp.name)
        self.row = dict(cell_id='x', eq='EQ1', group='gpu_delay', repeats='2',
                        resource_class='GPU_EXCLUSIVE', blocking_gate='G1-GPU',
                        minimum_configuration='yes')

    def test_missing_runs_preserve_repeats_and_do_not_create_attempts(self):
        rows = self.module.coverage_rows([self.row], self.results)
        self.assertEqual([r['run_id'] for r in rows], ['x-r001', 'x-r002'])
        self.assertEqual({r['state'] for r in rows}, {'PLANNED'})
        self.assertTrue(all(r['validation_state'] == 'NOT_RUN' for r in rows))
        self.assertEqual(list(self.results.iterdir()), [])

    def test_done_marker_without_evidence_cannot_count_as_validated_done(self):
        run = self.results/'runs/x-r001'
        attempt = run/'attempts/0001'
        attempt.mkdir(parents=True)
        (run/'status.json').write_text(json.dumps(dict(state='DONE', attempt='attempts/0001')))
        rows = self.module.coverage_rows([self.row], self.results)
        self.assertEqual(rows[0]['state'], 'DONE')
        self.assertEqual(rows[0]['validation_state'], 'INVALID_EVIDENCE')
        self.assertEqual(self.module.summarize(rows)['validated_done_runs'], 0)

    def test_unknown_state_is_reported_without_rewriting_source(self):
        run = self.results/'runs/x-r001'
        run.mkdir(parents=True)
        status = run/'status.json'
        original = json.dumps(dict(state='COMPLETE'))
        status.write_text(original)
        row = self.module.coverage_rows([self.row], self.results)[0]
        self.assertEqual(row['validation_state'], 'INVALID_EVIDENCE')
        self.assertEqual(status.read_text(), original)

    def test_fifo_status_is_rejected_before_open(self):
        run = self.results/'runs/x-r001'
        run.mkdir(parents=True)
        os.mkfifo(run/'status.json')
        import signal
        def timeout(*_):
            raise AssertionError('coverage blocked opening FIFO')
        old = signal.signal(signal.SIGALRM, timeout)
        signal.alarm(2)
        try:
            row = self.module.coverage_rows([self.row], self.results)[0]
            self.assertEqual(row['validation_state'], 'INVALID_EVIDENCE')
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    def test_required_unestimated_budget_and_input_fields_are_explicit(self):
        row = self.module.coverage_rows([self.row], self.results)[0]
        self.assertEqual(row.get('estimated_peak_bytes'), 'NOT_ESTIMATED')
        self.assertEqual(row.get('input_artifacts'), '[]')
        self.assertTrue(row.get('minimum_repair'))

    def test_real_sealed_fixture_and_matrix_binding(self):
        from test_run_matrix import MatrixRunnerTests
        fixture = MatrixRunnerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.execute()
        self.assertEqual(fixture.status()['state'], 'DONE')
        digest = fixture.manifests.sha256(fixture.matrix)
        rows = self.module.coverage_rows([fixture.row], fixture.results, matrix_sha256=digest)
        self.assertEqual(self.module.summarize(rows)['validated_done_runs'], 1)
        self.assertEqual(json.loads(rows[0]['task_artifacts']), fixture.task['artifacts'])
        wrong = self.module.coverage_rows([fixture.row], fixture.results, matrix_sha256='0'*64)
        self.assertEqual(wrong[0]['validation_state'], 'INVALID_EVIDENCE')
        changed = dict(fixture.row, rho='0.5')
        wrong = self.module.coverage_rows([changed], fixture.results, matrix_sha256=digest)
        self.assertEqual(wrong[0]['validation_state'], 'INVALID_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
