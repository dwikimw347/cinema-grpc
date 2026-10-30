"""Create tables, PNG figures, and a reviewable report from raw experiment files."""

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def save_figures(summary, concurrency):
    figure_dir = REPORTS / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    rows = summary["summary"]
    clients = [int(row["concurrent_clients"]) for row in rows]

    fig, axis = plt.subplots(figsize=(8, 5))
    for key, label in (("mean_latency_ms", "Mean"), ("p50_latency_ms", "p50"), ("p95_latency_ms", "p95")):
        axis.plot(clients, [float(row[key]) for row in rows], marker="o", linewidth=2, label=label)
    axis.set(title="Latency per Concurrent Client", xlabel="Concurrent clients", ylabel="Latency (ms)")
    axis.set_xticks(clients)
    axis.grid(True, alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(figure_dir / "latency_comparison.png", dpi=180)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(8, 5))
    values = [float(row["throughput_rps"]) for row in rows]
    bars = axis.bar([str(client) for client in clients], values, color="#68445E")
    axis.set(title="GetSeats Throughput", xlabel="Concurrent clients", ylabel="Requests per second (RPS)")
    axis.bar_label(bars, fmt="%.1f")
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figure_dir / "throughput_comparison.png", dpi=180)
    plt.close(fig)

    outcome_rows = []
    for row in rows:
        c = int(row["concurrent_clients"])
        outcome_rows.append((c,
            sum(int(x["success"]) for x in read_csv(REPORTS / "benchmark_results.csv") if int(x["concurrent_clients"]) == c),
            sum(int(x["business_rejection"]) for x in read_csv(REPORTS / "benchmark_results.csv") if int(x["concurrent_clients"]) == c),
            sum(int(x["technical_error"]) + int(x["timeouts"]) for x in read_csv(REPORTS / "benchmark_results.csv") if int(x["concurrent_clients"]) == c)))
    fig, axis = plt.subplots(figsize=(8, 5))
    width = 0.24
    positions = list(range(len(clients)))
    for offset, column, label, color in ((-width, 1, "Success", "#28714F"), (0, 2, "Business rejection", "#C48A23"),
                                          (width, 3, "Technical error / timeout", "#A03941")):
        bars = axis.bar([position + offset for position in positions], [row[column] for row in outcome_rows],
                        width=width, label=label, color=color)
        axis.bar_label(bars, padding=2)
    axis.set(title="Request Outcomes (GetSeats benchmark)", xlabel="Concurrent clients", ylabel="Requests (all repetitions)")
    axis.set_xticks(positions, [str(client) for client in clients])
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figure_dir / "request_outcomes.png", dpi=180)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(8, 5))
    summary = []
    for c in clients:
        selected = [row for row in concurrency if int(row["concurrent_clients"]) == c]
        n = len(selected)
        summary.append((c,
            sum(int(row["success"]) for row in selected) / n,
            sum(int(row["business_rejection"]) for row in selected) / n,
            sum(int(row["technical_error"]) for row in selected) / n))
    for offset, column, label, color in ((-width, 1, "Success / round", "#28714F"), (0, 2, "Business rejection / round", "#C48A23"),
                                          (width, 3, "Technical error / round", "#A03941")):
        bars = axis.bar([position + offset for position in positions], [row[column] for row in summary],
                        width=width, label=label, color=color)
        axis.bar_label(bars, padding=2)
    axis.set(title="Same Seat Contention Outcomes", xlabel="Concurrent clients", ylabel="Requests per round")
    axis.set_xticks(positions, [str(client) for client in clients])
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figure_dir / "contention_outcomes.png", dpi=180)
    plt.close(fig)


def build_report():
    benchmark = json.loads((REPORTS / "benchmark_summary.json").read_text(encoding="utf-8"))
    concurrency = read_csv(REPORTS / "concurrency_results.csv")
    faults = json.loads((REPORTS / "fault_tolerance_results.json").read_text(encoding="utf-8"))
    test_output = (REPORTS / "test_results.txt").read_text(encoding="utf-8")
    save_figures(benchmark, concurrency)

    lines = [
        "# Laporan Pengujian Cinema gRPC", "", "## Tujuan dan lingkungan", "",
        "Eksperimen menguji konsistensi perebutan kursi, idempotency saat respons hilang, pemulihan koneksi, streaming, dan performa RPC nyata.",
        "Server gRPC lokal menggunakan loopback serta SQLite sementara yang dibuat ulang pada setiap pengulangan. Tidak ada data `data/cinema.db` yang disentuh.",
        f"Python: `{benchmark['environment']['python'].splitlines()[0]}`; OS: `{benchmark['environment']['platform']}`; grpcio: `{benchmark['environment']['grpcio']}`; protobuf: `{benchmark['environment']['protobuf']}`.",
        "", "## Perintah reproduksi", "", "```powershell",
        ".\\.venv\\Scripts\\python.exe -m scripts.run_experiments",
        "```", "", "## Test suite", "", "```text", test_output.rstrip(), "```", "",
        "## Perebutan kursi", "", "Setiap round menggunakan jadwal `SCH001`, kursi `SCH001-A1`, request UUID unik, barrier serentak, dan DB baru. CSV juga mencatat durasi monotonic dan hasil query SQLite.", "",
        "| Client | Round | Request | Success | Rejection bisnis | Error teknis | Durasi (s) | DB confirmed | Status |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in concurrency:
        lines.append(f"| {row['concurrent_clients']} | {row['repetition']} | {row['total_requests']} | {row['success']} | {row['business_rejection']} | {row['technical_error']} | {float(row['duration_seconds']):.6f} | {row['db_confirmed']} | {row['status']} |")
    lines += ["", "## Benchmark GetSeats", "", "Warm-up: satu GetSeats dikeluarkan dari pengukuran per worker. Setiap tingkat concurrency mengirim jumlah request yang sama per pengulangan. Latency dihitung per RPC dengan `perf_counter`; throughput = seluruh RPC selesai dibagi waktu dinding dari pelepasan barrier sampai worker terakhir selesai. Business rejection tidak berlaku untuk operasi baca ini. Persentil dihitung interpolasi linear; nilai ringkasan adalah rata-rata antar pengulangan.", "",
              "| Concurrent clients | Total requests | Success | Business rejection | Technical errors | Mean latency (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (RPS) |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in benchmark["summary"]:
        lines.append("| " + " | ".join([
            str(row["concurrent_clients"]), str(row["total_requests"]), str(row["success"]),
            str(row["business_rejection"]), str(row["technical_error"] + row["timeouts"]),
            f"{row['mean_latency_ms']:.3f}", f"{row['p50_latency_ms']:.3f}",
            f"{row['p95_latency_ms']:.3f}", f"{row['p99_latency_ms']:.3f}",
            f"{row['throughput_rps']:.2f}"]) + " |")
    lines += ["", "![Latency comparison](figures/latency_comparison.png)", "",
              "![Throughput comparison](figures/throughput_comparison.png)", "",
              "![Request outcomes](figures/request_outcomes.png)", "",
              "![Contention outcomes](figures/contention_outcomes.png)", "",
              "## Fault tolerance", "", "Skenario commit-then-timeout menggunakan event hook setelah service menyelesaikan commit SQLite dan sebelum handler mengembalikan response. Status lulus berasal dari assertion yang dijalankan.", "",
              "| Skenario | Expected | Actual | Status |", "|---|---|---|---|"]
    for row in faults["scenarios"]:
        lines.append(f"| {row['scenario']} | `{json.dumps(row['expected'], ensure_ascii=False)}` | `{json.dumps(row['actual'], ensure_ascii=False)}` | {row['status']} |")
    lines += ["", "## Ringkasan dan keterbatasan", "",
              f"Concurrency: {sum(row['status'] == 'PASS' for row in concurrency)}/{len(concurrency)} round PASS. Fault tolerance: {faults['summary']['passed']}/{faults['summary']['passed'] + faults['summary']['failed']} skenario PASS.",
              f"Benchmark: {benchmark['repetitions']} pengulangan per tingkat, {benchmark['total_requests_per_repetition']} request per tingkat per pengulangan.",
              "Pengukuran dilakukan pada satu mesin dan loopback, sehingga angka bukan estimasi jaringan LAN/WAN atau multi-instance. Workload hanya GetSeats; contention dilaporkan terpisah. P99 pada 600 sampel gabungan per client cukup untuk estimasi kasar, tetapi jumlah sampel tetap terbatas untuk beban produksi.",
              "", "## Bukti mentah", "", "- `test_results.txt` — output unittest.", "- `concurrency_results.csv` — tiap ronde perebutan dan verifikasi DB.",
              "- `benchmark_latency_samples.csv` — latency/outcome setiap RPC; `benchmark_results.csv` dan `benchmark_summary.json` — ringkasan per ulangan dan metadata.",
              "- `fault_tolerance_results.json` — prasyarat, langkah, expected/actual, status, dan bukti tiap skenario.", ""]
    (REPORTS / "TEST_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    build_report()
