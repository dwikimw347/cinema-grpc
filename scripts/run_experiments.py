"""Run the relevant suite and all repeatable local experiments."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from scripts.build_experiment_reports import build_report
from scripts.benchmark import run_benchmark
from scripts.concurrency_experiment import run_concurrency
from scripts.fault_tolerance_experiment import run_fault_tolerance


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def run_suite(log_path):
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8",
                            errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sections = ["COMMAND: " + " ".join(command) + "\nEXIT CODE: " + str(result.returncode) +
                "\n\n" + result.stdout]
    print(result.stdout, end="")
    print(f"[{'PASS' if result.returncode == 0 else 'FAIL'}] unittest (exit={result.returncode})")
    passed = result.returncode == 0
    if shutil.which("node"):
        node_command = ["node", "tests/test_web_state.js"]
        node_result = subprocess.run(node_command, cwd=ROOT, text=True, encoding="utf-8",
                                     errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        sections.append("\nNODE COMMAND: " + " ".join(node_command) + "\nEXIT CODE: " +
                        str(node_result.returncode) + "\n\n" + node_result.stdout)
        print(node_result.stdout, end="")
        print(f"[{'PASS' if node_result.returncode == 0 else 'FAIL'}] JavaScript state test (exit={node_result.returncode})")
        passed = passed and node_result.returncode == 0
    else:
        sections.append("\nNODE TEST: SKIPPED (Node.js is not installed)\n")
    log_path.write_text("\n".join(sections), encoding="utf-8")
    return passed


def run_all(requests=200, repetitions=3, contention_repetitions=3):
    REPORTS.mkdir(parents=True, exist_ok=True)
    suite_passed = run_suite(REPORTS / "test_results.txt")
    concurrency_rows = run_concurrency(REPORTS / "concurrency_results.csv", contention_repetitions)
    fault_results = run_fault_tolerance(REPORTS / "fault_tolerance_results.json")
    benchmark = run_benchmark(REPORTS / "benchmark_results.csv", REPORTS / "benchmark_summary.json",
                              requests, repetitions)
    build_report()
    required_passed = suite_passed and all(row["status"] == "PASS" for row in concurrency_rows)
    required_passed = required_passed and fault_results["summary"]["failed"] == 0
    required_passed = required_passed and all(row["technical_error"] == 0 and row["timeouts"] == 0
                                               for row in benchmark["summary"])
    print(f"\nArtifacts: {REPORTS}")
    return 0 if required_passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=200,
                        help="jumlah request GetSeats per concurrency per pengulangan")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--contention-repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.requests < 20 or args.repetitions < 1 or args.contention_repetitions < 1:
        parser.error("requests minimal 20; jumlah pengulangan minimal 1")
    raise SystemExit(run_all(args.requests, args.repetitions, args.contention_repetitions))


if __name__ == "__main__":
    main()
