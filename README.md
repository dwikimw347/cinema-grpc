# Cinema gRPC — Reservasi Tiket Bioskop

Aplikasi reservasi kursi bioskop dengan gRPC, Protocol Buffers, SQLite, client Python, dan web frontend. Server menyediakan daftar jadwal, status kursi, booking idempoten, serta streaming perubahan kursi secara langsung.

## Arsitektur dan teknologi

- `proto/cinema.proto` adalah kontrak empat RPC `GetSchedules`, `GetSeats`, `BookSeat`, dan `StreamSeatStatus`.
- `server/` mengimplementasikan service, transaksi SQLite, penguncian perebutan kursi, dan pub/sub status kursi.
- `client/` menyediakan library dan CLI dengan retry untuk status transport sementara.
- `web/` berisi gateway HTTP, frontend HTML/CSS/JavaScript, dan aset film demo.
- `generated/` berisi kode Python hasil kompilasi protobuf.
- Python 3.13/3.14, gRPC, protobuf, SQLite, Matplotlib (untuk grafik laporan), unittest, dan JavaScript tanpa framework.

Satu booking mengubah status kursi dan mencatat hasil request dalam satu transaksi `BEGIN IMMEDIATE`. Database memiliki unique constraint untuk satu booking terkonfirmasi per kursi. Retry dengan UUID dan payload sama mengembalikan hasil tersimpan tanpa membuat booking atau event stream baru. UUID sama dengan payload berbeda menghasilkan `ALREADY_EXISTS`. Timeout tidak memastikan bahwa transaksi gagal; gunakan UUID dan payload lama untuk mengetahui hasilnya.

Server menggunakan `RLock` dalam satu instance proses dan constraint/transaksi SQLite. Sistem pub/sub tidak menyinkronkan beberapa instance server. Subscriber yang terputus dapat subscribe kembali; server mengirim snapshot status terkini, tanpa replay log event.

## Prasyarat dan instalasi (Windows PowerShell)

Gunakan Python yang kompatibel dengan wheel gRPC pada `requirements.txt` (project semula diverifikasi pada Python 3.14; eksperimen final pada Python yang tercatat di laporan). Dari folder project:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Jika ada beberapa instalasi Python, pilih interpreter yang sesuai sebelum membuat virtual environment. Folder `.venv` tidak disertakan dalam paket.

Untuk mengubah kontrak protobuf, jalankan:

```powershell
.\.venv\Scripts\python.exe scripts\generate_proto.py
```

## Menjalankan aplikasi

Terminal pertama menjalankan server. Database default dibuat pada `data/cinema.db` dan berisi tiga jadwal serta 20 kursi per jadwal.

```powershell
.\.venv\Scripts\python.exe -m server.main
```

Alamat default `localhost:50051`. Untuk database demo terpisah:

```powershell
.\.venv\Scripts\python.exe -m server.main --port 50052 --db data\demo.db
```

Terminal kedua menjalankan CLI atau gateway web:

```powershell
.\.venv\Scripts\python.exe -m client.cli --server localhost:50051
.\.venv\Scripts\python.exe web\server.py 8080
```

Buka `http://localhost:8080`. Server gRPC harus aktif terlebih dahulu. Aplikasi demo menggunakan koneksi tanpa TLS.

## Test suite

Test menggunakan server gRPC pada port lokal acak dan database sementara. Test tidak menulis ke database aplikasi.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests\test_web_state.js
```

Test Python mencakup kontrak RPC, transaksi/rollback, idempotency, status error, streaming, batas subscriber, concurrency, client, gateway web, serta demo timeout. Test JavaScript membutuhkan Node.js.

## Pengujian concurrency, fault tolerance, dan benchmark

Jalankan satu skenario secara terpisah:

```powershell
.\.venv\Scripts\python.exe -m scripts.concurrency_experiment --repetitions 3
.\.venv\Scripts\python.exe -m scripts.fault_tolerance_experiment
.\.venv\Scripts\python.exe scripts\demo_fault_tolerance.py --local
```

Benchmark `GetSeats` memakai request gRPC sungguhan, satu channel persisten per worker, warm-up yang tidak dihitung, dan tepat 200 request per concurrency untuk setiap pengulangan. Default menguji 1, 5, dan 20 client sebanyak tiga kali:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark --requests 200 --repetitions 3
```

Concurrency test memisahkan workload contention dari benchmark baca: seluruh pemesan dalam satu round merebut jadwal dan kursi sama, dimulai dengan barrier. Setiap round menggunakan DB bersih dan UUID operasi unik. Hasil respons diperiksa terhadap row booking, jumlah booking terkonfirmasi/ditolak, constraint kursi, dan status `BOOKED` di SQLite.

Untuk menjalankan suite dan seluruh eksperimen, membangun tabel/grafik, serta menulis laporan:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_experiments
```

Parameter request dan jumlah pengulangan dapat diatur. Setiap concurrency harus membagi rata jumlah request. Contoh smoke run yang lebih kecil:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_experiments --requests 100 --repetitions 1 --contention-repetitions 1
```

Runner keluar dengan kode nonzero jika test wajib, invariant database, skenario fault, atau request benchmark gagal.

## Definisi metrik

- Latency: selang `time.perf_counter()` tepat sebelum tiap RPC sampai response/error diterima.
- Mean, p50, p95, p99 dihitung dari latency per request; percentile memakai interpolasi linear. Ringkasan tabel adalah rata-rata metrik antar pengulangan.
- Throughput: jumlah RPC yang selesai dibagi waktu dinding dari pelepasan barrier worker sampai seluruh worker selesai. Warm-up tidak masuk numerator maupun durasi.
- Penolakan bisnis dicatat terpisah dari error transport. Workload benchmark memakai `GetSeats`, sehingga penolakan bisnis tidak diharapkan. `DEADLINE_EXCEEDED` dicatat sebagai timeout dan technical error.
- Rasio sukses = response sukses / seluruh request benchmark. Konsistensi database diperiksa melalui `PRAGMA integrity_check` dan tidak adanya booking selama workload baca.

## Hasil eksperimen aktual

Hasil lengkap yang dihasilkan dari eksperimen pada mesin ini tersedia di [reports/TEST_REPORT.md](reports/TEST_REPORT.md). Data per pengulangan berada di CSV/JSON dan grafik dibuat langsung dari data tersebut. Tabel berikut diisi oleh hasil eksperimen final.

| Concurrent clients | Total requests | Success | Business rejection | Technical errors | Mean latency (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (RPS) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
<!-- BENCHMARK_TABLE_START -->
| 1 | 600 | 600 | 0 | 0 | 4.865 | 4.796 | 5.314 | 5.767 | 205.59 |
| 5 | 600 | 600 | 0 | 0 | 20.506 | 20.599 | 22.425 | 22.975 | 241.13 |
| 20 | 600 | 600 | 0 | 0 | 79.738 | 82.076 | 85.955 | 104.673 | 235.50 |
<!-- BENCHMARK_TABLE_END -->

## Laporan dan struktur penting

- `reports/test_results.txt`: output terminal test suite dan exit code.
- `reports/concurrency_results.csv`: setiap round 1/5/20 client beserta hasil query SQLite.
- `reports/fault_tolerance_results.json`: prasyarat, langkah, expected/actual, PASS/FAIL, dan bukti skenario.
- `reports/benchmark_results.csv`: data hasil tiap ulangan.
- `reports/benchmark_latency_samples.csv`: latency dan outcome untuk setiap RPC yang diukur.
- `reports/benchmark_summary.json`: ringkasan metrik dan konfigurasi runtime.
- `reports/TEST_REPORT.md`: metode, hasil, tabel, analisis, dan keterbatasan.
- `reports/figures/`: grafik latency, throughput, request outcomes, dan contention.
- `scripts/run_experiments.py`: entry point seluruh eksperimen.

## Batas pengujian

Eksperimen berjalan pada satu komputer dengan loopback dan SQLite lokal. Angka performa bukan estimasi jaringan LAN/WAN atau beberapa instance server. Benchmark mengukur pembacaan `GetSeats`, sedangkan perebutan kursi dilaporkan sebagai eksperimen terpisah. P99 dari beberapa ratus sampel berguna sebagai ringkasan lokal, tetapi tidak merepresentasikan tail latency produksi. Demo timeout menggunakan hook deterministik dan bukan pemutusan jaringan fisik.
