# Cinema Ticket Reservation System - Bagian 1

Server reservasi tiket bioskop berbasis gRPC. Bagian ini mencakup kontrak
Protocol Buffers, empat RPC, SQLite, concurrency control, deduplikasi persisten,
streaming status kursi, dan pengujian. Belum mencakup aplikasi client reservasi,
GUI, pembayaran, pembatalan, autentikasi, atau deployment Docker.

## Teknologi

- Python **3.14** (diverifikasi menggunakan **3.14.6** di Windows x64).
- `grpcio==1.85.0`, `grpcio-tools==1.85.0`, `protobuf==7.36.2`.
- SQLite (`sqlite3` bawaan Python), `threading.RLock`, dan `queue.Queue`.
- `unittest` bawaan Python; pengujian integrasi menggunakan gRPC sungguhan.

## Struktur folder

```text
cinema-grpc/
|-- proto/cinema.proto
|-- generated/
|   |-- __init__.py
|   |-- cinema_pb2.py
|   `-- cinema_pb2_grpc.py
|-- server/
|   |-- __init__.py
|   |-- main.py
|   |-- service.py
|   |-- database.py
|   `-- seat_manager.py
|-- data/cinema.db              # Dibuat otomatis, tidak masuk Git
|-- scripts/
|   |-- generate_proto.py
|   `-- check_server.py         # Pemeriksaan read-only, bukan client lengkap
|-- tests/
|   |-- __init__.py
|   |-- support.py
|   |-- test_booking.py
|   `-- test_concurrency.py
|-- .gitignore
|-- requirements.txt
`-- README.md
```

## Instalasi di Windows 11 (PowerShell)

Buka PowerShell pada folder `cinema-grpc`. Pastikan `python` menunjuk Python 3.14:

```powershell
Set-Location 'D:\TUGAS KULIAH\PRAKTIKUM RPC\cinema-grpc'
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Jika ada beberapa Python, pilih interpreter 3.14, misalnya `py -3.14 -m venv .venv`
apabila Python Launcher tersedia. Seluruh perintah berikut menggunakan interpreter
virtual environment secara langsung; aktivasi dan perubahan execution policy
PowerShell tidak diperlukan.

Jika pembuatan venv gagal pada `ensurepip` karena direktori sementara tidak bisa
ditulis, gunakan direktori sementara lokal lalu ulangi:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Generate kode Protocol Buffers

```powershell
.\.venv\Scripts\python.exe scripts\generate_proto.py
```

Perintah menghasilkan dua file pada `generated/` dan menyesuaikan import relatif
agar dapat dipakai sebagai package Python. Jalankan ulang setiap kali `.proto`
berubah. Jangan mengedit file hasil generate secara manual.

## Jalankan server

Dari folder `cinema-grpc`, cukup satu perintah:

```powershell
.\.venv\Scripts\python.exe -m server.main
```

Alamat default **localhost:50051**. Log menampilkan alamat dan lokasi database.
Database dibuat otomatis di `data/cinema.db`, terlepas dari direktori kerja.
Seed terdiri dari tiga jadwal film dan masing-masing 20 kursi (A1-A5, B1-B5,
C1-C5, D1-D5). Jadwal menggunakan waktu demo tetap, bukan jadwal bioskop nyata.
Contoh ID: jadwal `SCH001`, kursi `SCH001-A1`.

Restart server mempertahankan booking. Seed berulang tidak menimpa status kursi.
Tekan **Ctrl+C** untuk menghentikan server: subscriber dibersihkan dan panggilan
aktif diberi waktu maksimal tiga detik untuk selesai.

Untuk mengakses server dari komputer lain pada tahap integrasi:

```powershell
.\.venv\Scripts\python.exe -m server.main --host 0.0.0.0 --port 50051
```

Client harus menggunakan IP komputer server, bukan `localhost`; izinkan port
50051 pada firewall jaringan praktikum. Server menggunakan koneksi insecure
tanpa TLS sesuai cakupan tugas ini.

Untuk memakai database demonstrasi terpisah tanpa mengubah data utama:

```powershell
.\.venv\Scripts\python.exe -m server.main --port 50052 --db data\demo.db
```

## Pastikan server berjalan

Biarkan terminal server terbuka. Buka terminal PowerShell kedua pada folder proyek:

```powershell
.\.venv\Scripts\python.exe scripts\check_server.py
```

Hasilnya menampilkan `Server terhubung. Jumlah jadwal: 3` beserta daftar jadwal.
Pemeriksaan ini benar-benar memanggil `GetSchedules` dengan timeout lima detik,
tidak mengubah database. Untuk alamat lain:

```powershell
.\.venv\Scripts\python.exe scripts\check_server.py --address localhost:50052
```

## Kontrak empat RPC

Service: **`cinema.CinemaService`**. Import untuk tahap client:
`from generated import cinema_pb2 as pb` dan
`from generated import cinema_pb2_grpc as pb_grpc`.

| RPC | Request | Respons dan perilaku |
| --- | --- | --- |
| `GetSchedules` | `GetSchedulesRequest` kosong | `GetSchedulesResponse.schedules`: ID, judul film, studio, waktu ISO 8601 dengan zona waktu |
| `GetSeats` | `GetSeatsRequest.schedule_id` | `GetSeatsResponse.seats`: ID kursi, nomor, ID jadwal, enum `AVAILABLE`/`BOOKED` |
| `BookSeat` | UUID `request_id`, `schedule_id`, `seat_id`, `customer_name` | ID booking, ID request, `success`, pesan, ID kursi, enum `CONFIRMED`/`REJECTED` |
| `StreamSeatStatus` | `StreamSeatStatusRequest.schedule_id` | Server streaming `SeatStatusEvent`: snapshot awal, lalu pembaruan setelah commit |

`SeatStatusEvent` memiliki `schedule_id`, `event_type`, `seats`, dan `timestamp`
ISO 8601 UTC. Event `SNAPSHOT` berisi seluruh 20 kursi pada jadwal terkait.
Event `UPDATE` berisi satu kursi dengan status terbaru. Tiap elemen `seats`
memuat `seat_id`, `schedule_id`, `seat_number`, dan `status`.

Kursi sudah dipesan menghasilkan respons bisnis biasa: `success=false`,
`booking_status=REJECTED`. Percobaan yang ditolak tetap mendapat `booking_id`
sebagai ID catatan percobaan, bukan bukti tiket berhasil dipesan.

| Kondisi error | Status gRPC |
| --- | --- |
| UUID tidak valid, field wajib kosong, teks > 200 karakter | `INVALID_ARGUMENT` |
| Jadwal/kursi tidak ada atau kursi tidak termasuk jadwal | `NOT_FOUND` |
| Request ID lama digunakan dengan payload berbeda | `ALREADY_EXISTS` |
| Batas subscriber tercapai atau antrean subscriber penuh | `RESOURCE_EXHAUSTED` |
| SQLite sementara tidak dapat diakses/terkunci melewati batas tunggu | `UNAVAILABLE` |
| Kegagalan integritas/operasi database lainnya | `INTERNAL` |

## Mekanisme konsistensi

### Lock dan transaksi

`SeatManager` memakai satu `RLock` untuk critical section booking: pemeriksaan
request, pemeriksaan status kursi, perubahan status, penyimpanan hasil, commit,
dan memasukkan update ke antrean subscriber. SQLite menjalankan
`BEGIN IMMEDIATE` sebelum pemeriksaan agar penulisan diserialisasi.

Perubahan `seats` dan pencatatan `bookings` berlangsung dalam satu transaksi.
Jika penyimpanan booking gagal, perubahan kursi di-rollback. Semua input SQL
menggunakan parameter, bukan interpolasi string.

Constraint tambahan mencakup request ID unik, pasangan jadwal/nomor kursi unik,
foreign key kursi/jadwal, validasi status, dan unique partial index untuk satu
booking `CONFIRMED` per kursi. Karena itu, sepuluh request berbeda yang merebut
kursi sama menghasilkan tepat satu `CONFIRMED` dan sembilan `REJECTED`.

Lock Python hanya melindungi **satu proses server**. SQLite juga memberi
perlindungan transaksi dan constraint, tetapi sistem pub/sub ini hanya
mengetahui booking dari instance sendiri. Proyek ini tidak mengklaim dukungan
beberapa instance server terkoordinasi atau database pada shared network drive.

### Deduplikasi persisten

Hasil booking valid yang diproses, baik `CONFIRMED` maupun `REJECTED`, disimpan
di tabel `bookings`, termasuk pesan dan payload. `request_id` dinormalisasi
menjadi UUID standar. Payload pembanding adalah `schedule_id`, `seat_id`, dan
`customer_name` persis seperti dikirim (termasuk perbedaan spasi/huruf nama).

- ID sama + payload sama: kembalikan seluruh hasil sebelumnya, tanpa booking
  baru dan tanpa event streaming tambahan.
- ID sama + payload berbeda: tolak dengan `ALREADY_EXISTS`.
- Restart: hasil tetap dikenali karena disimpan di SQLite.
- Request tidak valid atau resource tidak ditemukan: error gRPC, tidak dicatat
  sebagai percobaan booking yang selesai.

Client tahap berikutnya harus menyimpan UUID yang sama selama retry operasi
yang sama. Timeout client tidak membuktikan transaksi gagal; server mungkin
sudah commit. Gunakan ID baru hanya untuk operasi baru.

### Streaming dan backpressure

Snapshot awal dan registrasi subscriber dilakukan dalam lock yang sama dengan
booking/publish. Booking yang bersamaan pasti tercakup dalam snapshot atau
antrean update, sehingga tidak ada celah antara membaca snapshot dan subscribe.

Setiap subscriber memiliki antrean thread-safe sendiri, maksimal **64 event**.
Publish memakai `put_nowait()` setelah commit, tanpa menunggu client. Subscriber
lambat yang antreannya penuh dikeluarkan; saat handler melanjutkan, stream
berakhir dengan `RESOURCE_EXHAUSTED`. Client perlu subscribe ulang dan membaca
snapshot baru. Tidak ada replay log event persisten.

Pengiriman/yield dan penantian antrean dilakukan di luar lock. Pembatalan atau
disconnect membersihkan subscriber melalui callback dan `finally`; handler
memeriksa antrean dengan interval maksimal 0,25 detik saat idle. Perilaku deteksi
putus koneksi jaringan tetap mengikuti transport gRPC.

Server menggunakan **32 worker** dan membatasi **16 subscriber aktif** agar
stream tidak menghabiskan seluruh worker untuk RPC biasa. Ini implementasi
sinkron sederhana untuk praktikum, bukan server streaming skala besar.

## Automated test

Server utama tidak harus berjalan. Pengujian membuka server gRPC sendiri pada
port lokal acak dan database sementara terpisah, lalu membersihkannya.
Pengujian tidak memesan kursi pada `data/cinema.db`.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Cakupan 16 test:

1. Daftar tiga jadwal.
2. Dua puluh kursi per jadwal beserta statusnya.
3. Booking berhasil mengubah kursi menjadi `BOOKED`.
4. Pengulangan request mengembalikan hasil identik.
5. Konflik payload dengan ID sama ditolak.
6. Hasil berhasil dan ditolak tetap dikenali setelah restart server.
7. Validasi input dan resource tidak ditemukan.
8. Constraint database menolak booking aktif kedua secara langsung.
9. Rollback perubahan kursi saat INSERT booking gagal.
10. Dua subscriber menerima snapshot/update dan dibersihkan setelah cancel.
11. Streaming jadwal tidak dikenal ditolak.
12. Sepuluh pemesan bersamaan: tepat satu berhasil, sembilan ditolak.
13. Sepuluh request duplikat bersamaan: hanya satu catatan booking.
14. Batas stream tidak menghalangi booking unary.
15. Race antara subscribe/snapshot dan booking tidak melewatkan perubahan.
16. Antrean terbatas, subscriber lambat, isolasi jadwal, dan retry tanpa event ganda.

### Hasil verifikasi implementasi

Verifikasi pada 29 Oktober 2026 menggunakan Python 3.14.6 di Windows x64:

- `unittest`: **16 test lulus**, durasi eksekusi test 0,527 detik.
- `pip check`: `No broken requirements found.`
- Server entry point berhasil dijalankan di `localhost:50051` dan diperiksa
  menggunakan `scripts/check_server.py`: tiga jadwal dikembalikan.
- Database utama: tiga jadwal, 60 kursi, nol booking (pengujian memakai DB lain).
- SQLite `integrity_check`: `ok`; `foreign_key_check`: tidak ada pelanggaran.
- Ctrl+C menampilkan log `Server berhenti dengan aman`.
- Pemeriksaan kompilasi seluruh modul Python berhasil.

Durasi test di atas bukan benchmark latensi atau throughput aplikasi.

## Implementasi Bagian 2 (Client & Toleransi Gangguan)

Bagian 2 telah selesai diimplementasikan dengan komponen:
1. **Client Library (`client/cinema_client.py`)**:
   - `CinemaClient`: antarmuka gRPC lengkap (`get_schedules`, `get_seats`, `book_seat`, `stream_seat_status`).
   - Penanganan timeout terukur (default 3 detik).
   - Exponential backoff & jitter untuk retry otomatis saat terjadi `DEADLINE_EXCEEDED` atau `UNAVAILABLE`.
   - **Idempotency Key (`request_id`)**: penggunaan UUID yang sama di setiap percobaan retry, menjamin semantik *at-most-once* dan mencegah double booking.
2. **Interactive CLI Client (`client/cli.py`)**:
   - Memilih jadwal tayang film secara interaktif.
   - Visualisasi denah studio (Grid 4x5: baris A-D, kolom 1-5) dengan indikator warna status kursi.
   - Pemesanan kursi dengan input validasi dan log retry yang jelas.
   - **Mode Monitor Real-Time**: server streaming yang memperbarui denah kursi di layar secara instan saat ada reservasi dari client lain.
   - Demo langsung pengujian idempotensi retry.
3. **Skrip Demo & Pengujian**:
   - `scripts/demo_fault_tolerance.py`: pengujian otomatis timeout, idempotent retry, race condition, dan live streaming.
   - `scripts/run_two_clients_demo.py`: demonstrasi interaksi 2 client terdistribusi terhadap server.
   - `scripts/benchmark.py`: pengujian throughput dan latensi untuk 1, 5, dan 20 client konkuren.
4. **Multi-Container & Multi-Komputer**:
   - `Dockerfile.server`, `Dockerfile.client`, dan `docker-compose.yml`.
   - Petunjuk lengkap koneksi LAN / 2 komputer dan Docker di `PETUNJUK_DEMO.md`.
5. **Verifikasi Test Suite**:
   - `python -m unittest discover -s tests -v`: **22 test lulus** (16 server test + 6 client integration test).
