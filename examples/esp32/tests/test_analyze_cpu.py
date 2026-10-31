#!/usr/bin/env python3
"""Known-value checks for the CPU log analyzer; no device required."""
import unittest

from analyze_cpu import METRICS, summarize


def sample(at_us, window_us, values, accounted):
    fields = " ".join(f"{key}={value:.3f}" for key, value in zip(METRICS, values))
    return f"CPU t_us={at_us} window_us={window_us} {fields} accounted_core_pct={accounted:.3f}\n"


class CpuAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.lines = [
            "irrelevant serial output\n",
            "CPU enabled clock=ESP_TIMER counter_bits=64 cpu_mhz=160 cores=2 payload_logs=0\n",
            sample(2_000_000, 1_000_000, [10, 30, 20, 4, 6, 1], 198),
            sample(6_000_000, 3_000_000, [20, 60, 40, 8, 12, 2], 202),
        ]

    def test_duration_weighting_and_accounted_diagnostic(self):
        result = summarize(self.lines)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["window_duration_us"], 4_000_000)
        self.assertEqual(result["unsampled_gap_us"], 1_000_000)
        self.assertEqual(list(result["weighted_average_pct"].values()), [17.5, 52.5, 35, 7, 10.5, 1.75])
        self.assertEqual(list(result["max_sample_pct"].values()), [20, 60, 40, 8, 12, 2])
        self.assertEqual(result["accounted"]["weighted_average_pct"], 201)
        self.assertEqual(result["accounted"]["expected_pct"], 200)
        self.assertEqual(result["accounted"]["weighted_error_percentage_points"], 1)

    def test_filters_require_complete_windows(self):
        result = summarize(self.lines, 2_500_000, 6_000_000)
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["weighted_average_pct"]["chip_busy_pct"], 40)
        self.assertEqual(summarize(self.lines, 1_000_000, 2_000_000)["sample_count"], 1)
        with self.assertRaisesRegex(ValueError, "no complete CPU sample"):
            summarize(self.lines, 1_500_000, 5_500_000)

    def test_missing_config_and_sampler_diagnostics(self):
        result = summarize(self.lines[2:] + ["CPU invalid sample core=1\n"])
        self.assertNotIn("expected_pct", result["accounted"])
        self.assertEqual(result["diagnostic_lines"], [{"line": 3, "message": "CPU invalid sample core=1"}])

    def test_restarted_uptime_and_bad_values_fail(self):
        with self.assertRaisesRegex(ValueError, "restarted uptime"):
            summarize(self.lines + [self.lines[2]])
        for bad in ("nan", "inf", "-1", "oops"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "malformed CPU sample"):
                summarize([self.lines[2].replace("core0_busy_pct=10.000", f"core0_busy_pct={bad}")])


if __name__ == "__main__":
    unittest.main()
