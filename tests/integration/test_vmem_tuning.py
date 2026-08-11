#!/usr/bin/env python3
import csv
import contextlib
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/tune_vmem_profile.py"
SOURCE_CSV = pathlib.Path(
    "/home/victoryang00/nvme-mem2nvm/docs/superpowers/results/"
    "2026-07-30-vmem-sw-performance.csv")
SOURCE_SHA256 = (
    "4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f")
COMMITTED_PROFILE = ROOT / "configs/profiles/cd8p-vmem-p50.json"
COMMITTED_REPORT = ROOT / "configs/tuning/cd8p-vmem-comparison.json"

READ_QUANTILES_NS = {
    4096: (11133, 38238),
    16384: (41495, 43033),
    65536: (168606, 1247765),
    262144: (2824351, 3860958),
    1048576: (10767793, 11968167),
    2097152: (20254374, 22163673),
}


def load_module():
    spec = importlib.util.spec_from_file_location("tune_vmem", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_complete_rows():
    rows = []
    for size, (p50_ns, p95_ns) in READ_QUANTILES_NS.items():
        for sample, latency_ns in enumerate([p50_ns] * 10 + [p95_ns], 1):
            rows.append({
                "metric": "ssd_cold_fault_read",
                "tier": "ssd",
                "size_bytes": str(size),
                "sample": str(sample),
                "latency_us": str(latency_ns / 1000),
                "bandwidth_mib_s": "1",
            })
    for sample, latency_ns in enumerate([408305] * 10 + [596336], 1):
        rows.append({
            "metric": "ssd_fsync",
            "tier": "ssd",
            "size_bytes": "4096",
            "sample": str(sample),
            "latency_us": str(latency_ns / 1000),
            "bandwidth_mib_s": "1",
        })
    rows.extend([
        {"metric": "ram_hot_read", "tier": "ram", "size_bytes": "4096",
         "sample": "1", "latency_us": "0.036", "bandwidth_mib_s": "1"},
        {"metric": "ssd_cache_hot_read", "tier": "ssd", "size_bytes": "4096",
         "sample": "1", "latency_us": "0.045", "bandwidth_mib_s": "1"},
    ])
    return rows


def write_fixture(directory, rows=None):
    path = pathlib.Path(directory) / "source.csv"
    fields = ("metric", "tier", "size_bytes", "sample", "latency_us",
              "bandwidth_mib_s")
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows if rows is not None else make_complete_rows())
    return path


class VmemTuningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_nearest_rank_quantiles_and_capacity_alignment(self):
        summary = self.module.summarize(make_complete_rows())
        self.assertEqual(summary["read_curve"][0], {
            "pages": 1, "cumulative_ns": 11133, "p95_ns": 38238})
        self.assertEqual(summary["read_curve"][-1], {
            "pages": 512, "cumulative_ns": 20254374,
            "p95_ns": 22163673})
        self.assertEqual(summary["program_p50_ns"], 408305)
        self.assertEqual(summary["program_p95_ns"], 596336)
        self.assertEqual(
            self.module.align_capacity(
                1920383410176, 4096, 32, 8, 4, 256),
            1919850381312)

    def test_missing_duplicate_nonfinite_and_unknown_rows_fail(self):
        complete = make_complete_rows()
        missing = [row for row in complete
                   if not (row["metric"] == "ssd_fsync" and
                           row["sample"] == "11")]
        duplicate = complete + [dict(complete[0])]
        nonfinite = [dict(row) for row in complete]
        nonfinite[0]["latency_us"] = "nan"
        unknown = complete + [{
            "metric": "mystery", "tier": "ssd", "size_bytes": "4096",
            "sample": "1", "latency_us": "1", "bandwidth_mib_s": "1"}]
        for rows, message in (
            (missing, "missing samples"),
            (duplicate, "duplicate row"),
            (nonfinite, "latency must be finite and positive"),
            (unknown, "unknown metric"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    self.module.summarize(rows)

    def test_generation_is_atomic_and_provenance_bearing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = write_fixture(root)
            profile_path = root / "profile.json"
            report_path = root / "report.json"
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            result = self.module.generate(
                csv_path=source,
                base_profile=ROOT / "configs/profiles/nominal.json",
                profile_path=profile_path,
                report_path=report_path,
                expected_sha256=digest)
            profile = result["profile"]
            report = result["report"]
            self.assertEqual(profile["page_bytes"], 4096)
            self.assertEqual(profile["time_scale"], 1)
            self.assertEqual(profile["queue_depth"], 1)
            self.assertEqual(profile["empirical_vmem"]["source_sha256"], digest)
            self.assertEqual(profile["empirical_vmem"]["sample_count"], 11)
            self.assertTrue(report["all_breakpoints_exact"])
            self.assertEqual(len(report["comparisons"]), 6)
            self.assertTrue(profile_path.is_file())
            self.assertTrue(report_path.is_file())
            self.assertFalse((root / "profile.json.tmp").exists())
            self.assertFalse((root / "report.json.tmp").exists())

    def test_sha_mismatch_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = write_fixture(root)
            with self.assertRaisesRegex(ValueError, "source SHA256 mismatch"):
                self.module.generate(
                    csv_path=source,
                    base_profile=ROOT / "configs/profiles/nominal.json",
                    profile_path=root / "profile.json",
                    report_path=root / "report.json",
                    expected_sha256="0" * 64)
            self.assertFalse((root / "profile.json").exists())
            self.assertFalse((root / "report.json").exists())

    def test_committed_cd8p_artifacts_match_reviewed_source(self):
        self.assertTrue(COMMITTED_PROFILE.is_file())
        self.assertTrue(COMMITTED_REPORT.is_file())
        self.assertTrue(SOURCE_CSV.is_file())
        self.assertEqual(
            hashlib.sha256(SOURCE_CSV.read_bytes()).hexdigest(),
            SOURCE_SHA256)

        profile = json.loads(COMMITTED_PROFILE.read_text())
        report = json.loads(COMMITTED_REPORT.read_text())
        self.assertEqual(profile["capacity_bytes"], 1919850381312)
        self.assertEqual(profile["page_bytes"], 4096)
        self.assertEqual(profile["read_latency_ns"], 11133)
        self.assertEqual(profile["program_latency_ns"], 408305)
        self.assertEqual(profile["queue_depth"], 1)
        self.assertEqual(
            profile["aggregate_bandwidth_bytes_per_s"], 103540697)
        self.assertEqual(profile["hbm_cache_bytes"], 4294967296)
        self.assertEqual(profile["time_scale"], 1)
        self.assertEqual(
            profile["empirical_vmem"]["source_sha256"], SOURCE_SHA256)
        self.assertEqual(len(profile["empirical_vmem"]["read_curve"]), 6)
        self.assertEqual(report["effective_capacity_bytes"], 1919850381312)
        self.assertTrue(report["all_breakpoints_exact"])
        self.assertTrue(all(
            comparison["empirical_relative_error"] == 0.0
            for comparison in report["comparisons"]))

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fresh_profile = root / "cd8p-vmem-p50.json"
            fresh_report = root / "cd8p-vmem-comparison.json"
            with contextlib.chdir(ROOT):
                self.module.generate(
                    csv_path=SOURCE_CSV,
                    base_profile=pathlib.Path(
                        "configs/profiles/nominal.json"),
                    profile_path=fresh_profile,
                    report_path=fresh_report,
                    expected_sha256=SOURCE_SHA256)
            self.assertEqual(
                fresh_profile.read_bytes(), COMMITTED_PROFILE.read_bytes())
            normalized_report = fresh_report.read_text().replace(
                str(fresh_profile), "configs/profiles/cd8p-vmem-p50.json"
            ).replace(
                str(fresh_report),
                "configs/tuning/cd8p-vmem-comparison.json")
            self.assertEqual(normalized_report, COMMITTED_REPORT.read_text())


if __name__ == "__main__":
    unittest.main()
