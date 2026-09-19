"""Fixed software tests of the read-only parameter audit; no thermal runs."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
import eq3_parameter_check as audit

ROOT = Path(__file__).resolve().parents[1]


class ParameterAuditTest(unittest.TestCase):
    def test_actual_candidate(self):
        result = audit.check(ROOT)
        self.assertEqual(result['powered_regions'], 121)
        self.assertEqual(result['source_rank'], 17)
        self.assertEqual(result['cells']['1000'], 258048)
        self.assertEqual(result['input_energy_J']['train'], 1365)
        self.assertEqual(result['numerical_runs_started'], 0)

    def rejected(self, filename, mutate):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = Path('configs/eq3_thermal/research')
            shutil.copytree(ROOT / rel, root / rel)
            path = root / rel / filename
            data = json.loads(path.read_text())
            mutate(data)
            path.write_text(json.dumps(data))
            with self.assertRaises(AssertionError):
                audit.check(root)

    def test_missing_decision_field(self):
        self.rejected('parameter_registry.json', lambda d: d['parameters'][0].pop('consumer_path'))

    def test_overlapping_solids(self):
        self.rejected('candidate_profile.json', lambda d: d['blocks'].append(dict(d['blocks'][0], id='overlap')))

    def test_negative_power(self):
        self.rejected('calibration_power.json', lambda d: d['traces']['train']['slots_W'][0].__setitem__(0, -1))

    def test_invented_ocp_one_to_one_mapping(self):
        def change(d):
            for row in d['parameters']:
                if row['parameter_id']=='ocp.low':
                    row['selected_value_or_interval']['virtual_AXI_multiplicity']=1
        self.rejected('parameter_registry.json', change)


if __name__ == '__main__':
    unittest.main()
