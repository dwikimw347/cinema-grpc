"""Skrip pengujian kinerja RPC (Benchmark):
Mengukur latensi rata-rata (ms) dan throughput (req/detik) untuk 1, 5, dan 20 client bersamaan.
Sesuai poin rubrik praktikum RPC.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import sys
import time

# Pastikan root proyek masuk ke sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from client.cinema_client import CinemaClient

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run_benchmark(server_address="localhost:50051", total_requests_per_test=200):
    print(f"\n{CYAN}{'='*68}{RESET}")
    print(f"{BOLD}  PENGUJIAN KINERJA gRPC (LATENSI & THROUGHPUT BENCHMARK){RESET}")
    print(f"  Target Server: {YELLOW}{server_address}{RESET}")
    print(f"  Jumlah Permintaan per Tingkat Uji: {total_requests_per_test}")
    print(f"{CYAN}{'='*68}{RESET}\n")

    client_probe = CinemaClient(server_address)
    if not client_probe.ping(timeout=3.0):
        print(f"Server tidak dapat dijangkau di {server_address}. Harap jalankan server terlebih dahulu.")
        sys.exit(1)
    client_probe.close()

    concurrency_levels = [1, 5, 20]
    results = []

    for concurrency in concurrency_levels:
        latencies = []

        def worker_task(req_id):
            client = CinemaClient(server_address)
            start = time.perf_counter()
            client.get_seats("SCH001", timeout=5.0)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            client.close()
            return elapsed_ms

        print(f"Menjalankan pengujian dengan {BOLD}{concurrency} client bersamaan{RESET}...")
        test_start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            latencies = list(executor.map(worker_task, range(total_requests_per_test)))

        total_duration = time.perf_counter() - test_start
        throughput = total_requests_per_test / total_duration
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)

        results.append({
            "concurrency": concurrency,
            "total_time": total_duration,
            "throughput": throughput,
            "avg_latency": avg_latency,
            "min_latency": min_latency,
            "max_latency": max_latency,
        })

    # Cetak tabel ringkasan
    print(f"\n{BOLD}=== HASIL PENGUKURAN KINERJA gRPC ==={RESET}")
    header = f"| {'Client Bersamaan':<17} | {'Waktu Total (s)':<15} | {'Throughput (req/s)':<19} | {'Latensi Rata2 (ms)':<19} |"
    divider = "+" + "-" * 19 + "+" + "-" * 17 + "+" + "-" * 21 + "+" + "-" * 21 + "+"
    print(divider)
    print(header)
    print(divider)
    for r in results:
        print(f"| {r['concurrency']:<17} | {r['total_time']:<15.3f} | {r['throughput']:<19.1f} | {r['avg_latency']:<19.2f} |")
    print(divider)
    print(f"\n{GREEN}[SELESAI]{RESET} Tabel kinerja di atas siap dimasukkan ke dalam laporan praktikum.\n")


if __name__ == "__main__":
    addr = sys.argv[1] if len(sys.argv) > 1 else "localhost:50051"
    run_benchmark(addr)
