from pathlib import Path
import tempfile
import unittest

from analyze_uncontrolled_first_constraint import first_crossings, last_error_response


class FirstCrossingTests(unittest.TestCase):
    def test_last_error_skips_trailing_quit_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transcript.jsonl"
            path.write_text('{"response":{"type":"ERROR","status":"DOMAIN_FAILURE"}}\n'
                            '{"command":"QUIT"}\n')
            self.assertEqual(last_error_response(path)["status"], "DOMAIN_FAILURE")

    def test_ties_and_twenty_ms_interval(self):
        rows = [
            {"end_ns": 20_000_000, "thermal": {"temperatures": {
                "hbf0": 352.0, "hbf1": 352.0, "gpu": 350.0}}},
            {"end_ns": 40_000_000, "thermal": {"temperatures": {
                "hbf0": 354.0, "hbf1": 354.0, "gpu": 350.0}}},
        ]
        result = first_crossings(rows, {
            "hbf": [353.15, 363.15, 378.15], "hbm": [353.15, 363.15, 378.15],
            "gpu": [363.15, 373.15, 383.15]})
        self.assertEqual(result["light"]["end_ns"], 40_000_000)
        self.assertEqual(result["light"]["interval_ns"], {
            "lower_exclusive": 20_000_000, "upper_inclusive": 40_000_000})
        self.assertEqual(result["light"]["tied_owners"], ["hbf0", "hbf1"])
        self.assertIsNone(result["severe"])


if __name__ == "__main__":
    unittest.main()
