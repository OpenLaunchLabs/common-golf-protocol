#!/usr/bin/env python3
"""Summarize ESP32 cpu_stats.c serial output using only complete sample windows.

Usage: python analyze_cpu.py serial.log --start 30 --end 90

Bounds are seconds since boot. A sample covers [t_us - window_us, t_us];
both boundaries must fall inside the requested interval. Averages are weighted
by window_us. Maxima are the largest window averages, not instantaneous peaks.
Core/task percentages use one core as 100%; chip_busy_pct uses the whole chip.
The accounted diagnostic sums all task runtimes, including idle, and should be
near 100% times the core count. Task switching and snapshot timing affect it.
"""
import argparse
import json
import math
from pathlib import Path
import re


METRICS = (
    "core0_busy_pct", "core1_busy_pct", "chip_busy_pct", "main_core_pct",
    "network_core_pct", "profiler_core_pct",
)
ACCOUNTED = "accounted_core_pct"
FIELDS = re.compile(r"\b([a-zA-Z0-9_]+)=([^\s]+)")


def summarize(lines, start_us=None, end_us=None):
    samples = []
    config = {}
    diagnostics = []
    previous_end = None
    for line_number, line in enumerate(lines, 1):
        if "CPU enabled " in line:
            config.update(dict(FIELDS.findall(line.split("CPU enabled ", 1)[1])))
        if any(marker in line for marker in (
            "CPU invalid sample ", "CPU snapshot failed", "CPU profiler task allocation failed"
        )):
            diagnostics.append({"line": line_number, "message": line.strip()})
        if "CPU t_us=" not in line:
            continue
        fields = dict(FIELDS.findall(line.split("CPU ", 1)[1]))
        try:
            at_us = int(fields["t_us"])
            window_us = int(fields["window_us"])
            values = {name: float(fields[name]) for name in (*METRICS, ACCOUNTED)}
            if window_us <= 0 or at_us < window_us:
                raise ValueError("invalid sample window")
            if not all(math.isfinite(value) and value >= 0 for value in values.values()):
                raise ValueError("percentages must be finite and nonnegative")
        except (KeyError, ValueError) as error:
            raise ValueError(f"line {line_number}: malformed CPU sample ({error})") from error
        if previous_end is not None and at_us - window_us < previous_end:
            raise ValueError(
                f"line {line_number}: overlapping windows or restarted uptime; "
                "analyze one boot/capture at a time"
            )
        previous_end = at_us
        samples.append((at_us, window_us, values))

    selected = [sample for sample in samples
                if (start_us is None or sample[0] - sample[1] >= start_us)
                and (end_us is None or sample[0] <= end_us)]
    if not selected:
        raise ValueError("no complete CPU sample windows inside the requested interval")
    duration_us = sum(sample[1] for sample in selected)

    def average(name):
        return math.fsum(sample[1] * sample[2][name] for sample in selected) / duration_us

    first_start_us = selected[0][0] - selected[0][1]
    last_end_us = selected[-1][0]
    accounted = {
        "weighted_average_pct": average(ACCOUNTED),
        "min_sample_pct": min(sample[2][ACCOUNTED] for sample in selected),
        "max_sample_pct": max(sample[2][ACCOUNTED] for sample in selected),
    }
    if "cores" in config:
        try:
            cores = int(config["cores"])
            if cores <= 0:
                raise ValueError("core count must be positive")
        except ValueError as error:
            raise ValueError(f"invalid CPU enabled core count: {config['cores']}") from error
        accounted["expected_pct"] = 100 * cores
        accounted["weighted_error_percentage_points"] = average(ACCOUNTED) - 100 * cores

    return {
        "sample_count": len(selected),
        "total_sample_count": len(samples),
        "window_duration_us": duration_us,
        "window_duration_seconds": duration_us / 1_000_000,
        "first_window_start_us": first_start_us,
        "last_window_end_us": last_end_us,
        "unsampled_gap_us": last_end_us - first_start_us - duration_us,
        "weighted_average_pct": {name: average(name) for name in METRICS},
        "max_sample_pct": {name: max(sample[2][name] for sample in selected) for name in METRICS},
        "accounted": accounted,
        "profiler_config": config,
        "diagnostic_lines": diagnostics,
    }


def uptime_seconds(value):
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be finite, nonnegative seconds") from error
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("must be finite, nonnegative seconds")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, help="serial log containing CPU t_us=... lines")
    parser.add_argument("--start", type=uptime_seconds, help="inclusive window start, seconds since boot")
    parser.add_argument("--end", type=uptime_seconds, help="inclusive window end, seconds since boot")
    args = parser.parse_args()
    if args.start is not None and args.end is not None and args.end <= args.start:
        parser.error("--end must be greater than --start")
    try:
        with args.log.open(encoding="utf-8", errors="replace") as log:
            result = summarize(
                log,
                None if args.start is None else round(args.start * 1_000_000),
                None if args.end is None else round(args.end * 1_000_000),
            )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    result["source"] = str(args.log)
    result["requested_uptime_seconds"] = {"start": args.start, "end": args.end}
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
