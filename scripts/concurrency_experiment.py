"""Reproducible gRPC race for one seat using a fresh SQLite database per round."""

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import uuid

import grpc

from generated import cinema_pb2 as pb
from generated import cinema_pb2_grpc as pb_grpc
from scripts.experiment_support import booking_counts, local_server


def run_round(concurrency):
    with local_server() as (address, manager):
        channel = grpc.insecure_channel(address)
        stub = pb_grpc.CinemaServiceStub(channel)
        started_at = [None]
        barrier = threading.Barrier(concurrency, action=lambda: started_at.__setitem__(0, time.perf_counter()))

        def request(index):
            request = pb.BookSeatRequest(
                request_id=str(uuid.uuid4()), schedule_id="SCH001",
                seat_id="SCH001-A1", customer_name=f"Contender {index}",
            )
            barrier.wait(timeout=10)
            started = time.perf_counter()
            try:
                response = stub.BookSeat(request, timeout=10)
                return ("success" if response.success else "business_rejection",
                        (time.perf_counter() - started) * 1000)
            except grpc.RpcError as error:
                return ("technical_error", (time.perf_counter() - started) * 1000, error.code().name)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            outcomes = list(executor.map(request, range(concurrency)))
        duration = time.perf_counter() - started_at[0]
        channel.close()

        counts = booking_counts(manager)
        result = {
            "concurrent_clients": concurrency,
            "total_requests": concurrency,
            "success": sum(outcome[0] == "success" for outcome in outcomes),
            "business_rejection": sum(outcome[0] == "business_rejection" for outcome in outcomes),
            "technical_error": sum(outcome[0] == "technical_error" for outcome in outcomes),
            "duration_seconds": duration,
            "mean_latency_ms": sum(outcome[1] for outcome in outcomes) / len(outcomes),
            **{f"db_{key}": value for key, value in counts.items()},
        }
        expected = result["success"] == 1 and result["business_rejection"] == concurrency - 1
        expected = expected and result["technical_error"] == 0
        expected = expected and counts["total"] == concurrency and counts["confirmed"] == 1
        expected = expected and counts["rejected"] == concurrency - 1
        expected = expected and counts["seat_status"] == "BOOKED" and counts["duplicate_confirmed_seats"] == 0
        if not expected:
            raise AssertionError(f"Race invariant failed: {result}")
        return result


def run_concurrency(output_path, repetitions=3, levels=(1, 5, 20)):
    rows = []
    for clients in levels:
        for repetition in range(1, repetitions + 1):
            row = run_round(clients)
            row["repetition"] = repetition
            row["status"] = "PASS"
            rows.append(row)
            print(f"[PASS] contention clients={clients} repetition={repetition}: "
                  f"{row['success']} success, {row['business_rejection']} rejected, "
                  f"{row['technical_error']} technical errors, DB={row['db_confirmed']} confirmed")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/concurrency_results.csv"))
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    run_concurrency(args.output, args.repetitions)


if __name__ == "__main__":
    main()
