import csv
import tempfile
import unittest
from pathlib import Path

from eq3_acceptance_v3 import analyze


def method(end=4.0, windows=None, threshold=301.0, minimum=.2):
    return {"schema_version":"eq3-acceptance-v3-method-v1", "temperature_unit":"K",
            "time_unit":"ns", "sensor_ids":["s"], "initial_time_s":0,
            "initial_temperature_k":300.0,
            "windows":windows or [
                {"id":"full","kind":"full","start_s":0,"end_s":end},
                {"id":"heat","kind":"excitation","start_s":0,"end_s":end/2},
                {"id":"cool","kind":"cooling","start_s":end/2,"end_s":end}],
            "hotspot_sensor_ids":["s"], "control_sensor_ids":[],
            "crossing_thresholds_k":[threshold], "crossing_min_time_s":minimum,
            "crossing_fraction":.05, "quantization_k":.001,
            "quantization_mode":"nearest_rounding"}


ENERGY={"total_input_energy_j":10,"stored_energy_change_j":4,"boundary_loss_j":6}


class AcceptanceV3Tests(unittest.TestCase):
    def run_case(self, ref, cand, selected=None):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); rp=root/'r.csv'; cp=root/'c.csv'
            for path,rows in ((rp,ref),(cp,cand)):
                with path.open('w',newline='') as out:
                    writer=csv.writer(out);writer.writerow(['time_s','sensor_id','temperature_k'])
                    for t,v in rows:writer.writerow([t,'s',v])
            return analyze(rp,cp,selected or method(end=max(ref[-1][0],cand[-1][0])),ENERGY)

    def crossing(self,result):
        return result['crossing_scores'][0]

    def test_same_window_up_crossing_passes(self):
        rows=[(1,300.5),(2,301.5),(4,302)]
        result=self.run_case(rows,rows)
        self.assertEqual(self.crossing(result)['status'],'PASS')
        self.assertEqual(self.crossing(result)['matches'][0]['direction'],'up')

    def test_boundary_event_matches_globally_then_is_indeterminate_once(self):
        windows=[{"id":"full","kind":"full","start_s":0,"end_s":40},
                 {"id":"heat","kind":"excitation","start_s":0,"end_s":39},
                 {"id":"cool","kind":"cooling","start_s":39,"end_s":40}]
        ref=[(38.9,300.9),(39.0,301.0),(39.1,301.1),(40,301.2)]
        cand=[(38.9,300.9),(39.0,300.99),(39.1,301.1),(40,301.2)]
        result=self.run_case(ref,cand,method(40,windows))
        crossing=self.crossing(result)
        self.assertEqual(crossing['status'],'INDETERMINATE_QUANTIZATION')
        self.assertEqual(len(crossing['matches']),1)
        assignment=crossing['matches'][0]['window_assignment']
        self.assertEqual(assignment['status'],'INDETERMINATE_WINDOW_ASSIGNMENT')
        self.assertIn('full',assignment['certain_window_ids'])

    def test_threshold_plateau_return_is_not_pass(self):
        ref=[(1,300.9),(2,301.0),(3,300.9),(4,300.8)]
        result=self.run_case(ref,ref)
        self.assertEqual(self.crossing(result)['status'],'INDETERMINATE_QUANTIZATION')
        self.assertTrue(self.crossing(result)['reference_indeterminate'])

    def test_exact_equal_between_opposite_states_is_interval_event(self):
        rows=[(1,300.9),(2,301.0),(3,301.1),(5,301.2)]
        windows=[{"id":"full","kind":"full","start_s":0,"end_s":5},
                 {"id":"heat","kind":"excitation","start_s":0,"end_s":4},
                 {"id":"cool","kind":"cooling","start_s":4,"end_s":5}]
        crossing=self.crossing(self.run_case(rows,rows,method(5,windows)))
        self.assertEqual(crossing['status'],'PASS')
        self.assertTrue(crossing['reference_events'][0]['quantization_bridged'])
        self.assertLess(crossing['reference_events'][0]['time_lo_ns'],2_000_000_000)
        self.assertGreater(crossing['reference_events'][0]['time_hi_ns'],2_000_000_000)

    def test_ambiguous_excursion_cannot_be_called_definite_missing(self):
        ref=[(1,300.0),(2,302.0),(4,302.0)]
        cand=[(1,300.0),(2,301.0),(4,300.0)]
        crossing=self.crossing(self.run_case(ref,cand))
        self.assertEqual(crossing['status'],'INDETERMINATE_QUANTIZATION')
        self.assertIsNone(crossing['failure_reason'])

    def test_no_crossing_is_not_applicable(self):
        rows=[(1,300),(2,300.2),(4,300.3)]
        self.assertEqual(self.crossing(self.run_case(rows,rows))['status'],'NOT_APPLICABLE')

    def test_one_sided_definite_missing_crossing_fails(self):
        ref=[(1,300),(2,302),(4,302)]
        cand=[(1,300),(2,300.2),(4,300.3)]
        crossing=self.crossing(self.run_case(ref,cand))
        self.assertEqual(crossing['status'],'FAIL')
        self.assertEqual(crossing['failure_reason'],'DEFINITE_MISSING_OR_EXTRA_CROSSING')

    def test_multiple_oscillations_keep_direction_and_order(self):
        rows=[(.5,300),(1,302),(1.5,300),(2,302),(4,302)]
        crossing=self.crossing(self.run_case(rows,rows))
        self.assertEqual([x['direction'] for x in crossing['reference_events']],['up','down','up'])
        self.assertEqual(crossing['status'],'PASS')

    def test_different_sampling_and_integer_time_normalization(self):
        ref=[(.3,300.0),(1.0,302.0),(2.0,302.0),(4.0,302.0)]
        cand=[(.30000000000000004,300.0),(.5,300.5),(1.0,302.0),(3.0,302.0),(4.0,302.0)]
        result=self.run_case(ref,cand)
        self.assertEqual(self.crossing(result)['status'],'PASS')
        self.assertAlmostEqual(result['temperature_scores'][0]['time_weighted_mae_k'],.00625)

    def test_cooling_direction_and_timeout_failure(self):
        selected=method(threshold=301,minimum=.1)
        ref=[(1,302),(2,300),(4,300)]
        cand=[(1,302),(3,302),(4,300)]
        crossing=self.crossing(self.run_case(ref,cand,selected))
        self.assertEqual(crossing['reference_events'][-1]['direction'],'down')
        self.assertEqual(crossing['status'],'FAIL')
        self.assertEqual(crossing['failure_reason'],'CROSSING_TIMEOUT')

    def test_temperature_and_energy_limits_are_unchanged(self):
        rows=[(1,300),(2,300),(4,300)]
        result=self.run_case(rows,[(1,303),(2,303),(4,303)])
        self.assertEqual(result['status'],'NUMERICAL_FAIL')
        self.assertEqual(result['unchanged_limits']['energy_relative_limit'],.001)
        self.assertEqual(result['unchanged_limits']['hotspot_control_max_k'],2.0)


if __name__=='__main__':unittest.main()
