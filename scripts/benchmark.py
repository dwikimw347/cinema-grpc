"""Measure real GetSeats RPC throughput and latency on isolated local servers."""

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import json
import platform
from pathlib import Path
import statistics
import sys
import threading
import time

import grpc
import google.protobuf

from generated import cinema_pb2 as pb
from generated import cinema_pb2_grpc as pb_grpc
from scripts.experiment_support import local_server


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def run_trial(concurrency, total_requests):
    if total_requests < concurrency or total_requests % concurrency:
        raise ValueError("total_requests harus >= concurrency dan habis dibagi concurrency")
    with local_server() as (address, manager):
        started_at = [None]
        barrier = threading.Barrier(concurrency, action=lambda: started_at.__setitem__(0, time.perf_counter()))

        def worker(worker_id):
            channel = grpc.insecure_channel(address)
            stub = pb_grpc.CinemaServiceStub(channel)
            request = pb.GetSeatsRequest(schedule_id="SCH001")
            # Satu warm-up per channel, dikeluarkan dari sampel dan durasi.
            warmup = stub.GetSeats(request, timeout=5)
            if len(warmup.seats) != 20:
                raise AssertionError("Warm-up menerima jumlah kursi yang tidak sesuai")
            barrier.wait(timeout=10)
            samples = []
            outcomes = []
            for _ in range(total_requests // concurrency):
                started = time.perf_counter()
                try:
                    response = stub.GetSeats(request, timeout=5)
                    samples.append((time.perf_counter() - started) * 1000)
                    outcomes.append("success" if len(response.seats) == 20 else "technical_error")
                except grpc.RpcError as error:
                    samples.append((time.perf_counter() - started) * 1000)
                    outcomes.append("timeout" if error.code() == grpc.StatusCode.DEADLINE_EXCEEDED else "technical_error")
            channel.close()
            return samples, outcomes

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            workers = list(executor.map(worker, range(concurrency)))
        duration = time.perf_counter() - started_at[0]
        samples = [sample for worker_samples, _ in workers for sample in worker_samples]
        outcomes = [outcome for _, worker_outcomes in workers for outcome in worker_outcomes]
        with manager.database.connect() as connection:
            bookings = connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if bookings != 0 or integrity != "ok":
            raise AssertionError(f"Database benchmark tidak konsisten: {bookings=}, {integrity=}")
        success = outcomes.count("success")
        return {
            "concurrent_clients": concurrency,
            "total_requests": total_requests,
            "success": success,
            "business_rejection": 0,
            "technical_error": outcomes.count("technical_error"),
            "timeouts": outcomes.count("timeout"),
            "mean_latency_ms": statistics.fmean(samples),
            "p50_latency_ms": percentile(samples, 0.50),
            "p95_latency_ms": percentile(samples, 0.95),
            "p99_latency_ms": percentile(samples, 0.99) if len(samples) >= 100 else None,
            "throughput_rps": len(outcomes) / duration,
            "duration_seconds": duration,
            "success_ratio": success / len(outcomes),
            "db_bookings": bookings,
            "db_integrity": integrity,
            "latency_samples": [
                {"worker": worker_id, "request_index": request_index,
                 "latency_ms": latency, "outcome": outcome}
                for worker_id, (worker_samples, worker_outcomes) in enumerate(workers)
                for request_index, (latency, outcome) in enumerate(zip(worker_samples, worker_outcomes), start=1)
            ],
        }


def run_benchmark(output_path, summary_path, total_requests=200, repetitions=3, levels=(1, 5, 20)):
    rows = []
    raw_samples = []
    for clients in levels:
        for repetition in range(1, repetitions + 1):
            row = run_trial(clients, total_requests)
            row["repetition"] = repetition
            raw_samples.extend({"concurrent_clients": clients, "repetition": repetition, **sample}
                               for sample in row.pop("latency_samples"))
            rows.append(row)
            print(f"[{'PASS' if row['technical_error'] == row['timeouts'] == 0 else 'FAIL'}] "
                  f"benchmark clients={clients} run={repetition}: {row['success']}/{row['total_requests']} "
                  f"success, {row['throughput_rps']:.2f} RPS")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    samples_path = output_path.with_name("benchmark_latency_samples.csv")
    with samples_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(raw_samples[0]))
        writer.writeheader()
        writer.writerows(raw_samples)

    summary = []
    for clients in levels:
        selected = [row for row in rows if row["concurrent_clients"] == clients]
        samples = [row for row in selected]
        summary.append({
            "concurrent_clients": clients,
            "total_requests": sum(row["total_requests"] for row in samples),
            "success": sum(row["success"] for row in samples),
            "business_rejection": sum(row["business_rejection"] for row in samples),
            "technical_error": sum(row["technical_error"] for row in samples),
            "timeouts": sum(row["timeouts"] for row in samples),
            "mean_latency_ms": statistics.fmean(row["mean_latency_ms"] for row in samples),
            "p50_latency_ms": statistics.fmean(row["p50_latency_ms"] for row in samples),
            "p95_latency_ms": statistics.fmean(row["p95_latency_ms"] for row in samples),
            "p99_latency_ms": statistics.fmean(row["p99_latency_ms"] for row in samples),
            "throughput_rps": statistics.fmean(row["throughput_rps"] for row in samples),
            "duration_seconds": sum(row["duration_seconds"] for row in samples),
            "success_ratio": sum(row["success"] for row in samples) / sum(row["total_requests"] for row in samples),
            "db_bookings": sum(row["db_bookings"] for row in samples),
            "db_integrity": "ok" if all(row["db_integrity"] == "ok" for row in samples) else "FAIL",
            "repetitions": repetitions,
        })
    metadata = {
        "workload": "GetSeats(SCH001), one persistent gRPC channel per worker, fresh SQLite DB per repetition",
        "warmup": "one excluded GetSeats RPC per worker before synchronized start",
        "total_requests_per_repetition": total_requests,
        "repetitions": repetitions,
        "clock": "time.perf_counter (monotonic)",
        "throughput_formula": "completed RPCs / elapsed wall-clock seconds from barrier release to final response",
        "latency_formula": "elapsed time from immediately before each RPC until response/error; successful and failed attempts are samples",
        "percentile_method": "linear interpolation over sorted per-RPC latency samples; mean of repetition percentiles in summary",
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "grpcio": grpc.__version__, "protobuf": google.protobuf.__version__},
        "summary": summary,
    }
    summary_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark_results.csv"))
    parser.add_argument("--summary", type=Path, default=Path("reports/benchmark_summary.json"))
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    run_benchmark(args.output, args.summary, args.requests, args.repetitions)


if __name__ == "__main__":
    main()
