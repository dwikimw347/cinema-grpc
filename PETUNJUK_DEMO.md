# Petunjuk Menjalankan Demo & Dokumentasi Teknis
## Sistem Reservasi Bioskop Terdistribusi Berbasis gRPC

Sistem reservasi tiket bioskop terdistribusi ini mengimplementasikan komunikasi client–server berbasis **gRPC (Protocol Buffers)**, toleransi kegagalan dengan **idempotent retry & timeout**, kontrol konkuren menggunakan **mutex dan transaksi SQLite**, serta pembaruan status kursi secara langsung (**real-time server streaming**).

---

## 1. Fitur Utama Client & Integrasi Server

| Fitur | RPC / Mekanisme | Penjelasan |
|---|---|---|
| **Pilih Jadwal** | `GetSchedules` | Mengambil daftar jadwal film yang sedang tayang (ID, judul, studio, jam tayang). |
| **Lihat Denah Kursi** | `GetSeats` | Visualisasi antarmuka teks studio (Baris A–D, Kolom 1–5) dengan penanda `[ A1 ]` (tersedia) dan `[A1*]` (terisi). |
| **Pesan Kursi** | `BookSeat` | Pemesanan kursi dengan parameter `schedule_id`, `seat_id`, `customer_name`, dan `request_id`. |
| **Pembaruan Real-Time** | `StreamSeatStatus` | Server streaming mengirim event `SNAPSHOT` (kondisi awal) dan event `UPDATE` secara langsung saat ada perubahan status kursi. |
| **Toleransi Gangguan** | Timeout & Retry Idempoten | Client menerapkan batas waktu (timeout). Jika terjadi timeout atau server sementara tidak tersedia (`DEADLINE_EXCEEDED` / `UNAVAILABLE`), client melakukan retry dengan **Request ID yang SAMA** (Semantik *At-Most-Once*). |

---

## 2. Penanganan Gangguan & Toleransi Kegagalan

### a. Timeout & Idempotent Retry (Semantik At-Most-Once)
- Pada jaringan komputer nyata, jika client mengirim permintaan pemesanan lalu mengalami timeout (misalnya koneksi tersendat), client tidak dapat mengetahui apakah request sudah dieksekusi di server atau belum.
- Jika client mengulang dengan ID baru, dapat terjadi **double booking** (pemesanan berulang / kursi terambil dua kali).
- **Solusi**: Setiap pemesanan digenerate sebuah UUID unik (`request_id`). Saat terjadi retry otomatis, client **wajib mempertahankan UUID yang persis sama**.
- Di sisi server, SQLite menyimpan seluruh riwayat `request_id` di tabel `bookings`. Permintaan ulang dengan ID yang sama langsung mengembalikan status dan respons yang identik tanpa memproses reservasi baru.

### b. Concurrency Control (Mencegah Race Condition)
- Server menggunakan Python `threading.RLock` dan transaksi `BEGIN IMMEDIATE` pada SQLite.
- Database dilengkapi partial unique index untuk mencegah dua booking aktif (`CONFIRMED`) pada kursi yang sama.
- Jika 5 client memesan kursi yang sama pada milidetik yang sama, server menjamin tepat **1 client berhasil (`CONFIRMED`)** dan **4 client ditolak (`REJECTED`)**.

---

## 3. Petunjuk Menjalankan Demo

### Skenario 1: Demo Cepat di 1 Komputer (2 atau 3 Terminal)

#### Langkah 1: Jalankan Server (Terminal 1)
```powershell
# Buka folder proyek
cd "cinema-grpc"

# Jalankan server gRPC di port 50051
python -m server.main --host 0.0.0.0 --port 50051
```
*Server akan mencetak log bahwa layanan aktif di port 50051.*

#### Langkah 2: Jalankan Client 1 (Terminal 2)
```powershell
cd "cinema-grpc"

# Jalankan client interaktif
python -m client.cli --server localhost:50051
```
1. Pilih menu `1` untuk memilih film.
2. Pilih menu `4` untuk masuk ke **Mode Monitor Real-Time (Server Streaming)**. Biarkan terminal ini terbuka memantau denah kursi.

#### Langkah 3: Jalankan Client 2 (Terminal 3)
```powershell
cd "cinema-grpc"

# Jalankan client kedua
python -m client.cli --server localhost:50051
```
1. Pilih menu `1` untuk memilih film yang sama.
2. Pilih menu `3` untuk memesan kursi (misal kursi `B3`).
3. **Amati Terminal 2**: Kursi `B3` di Client 1 langsung berubah warna / status menjadi `[B3*]` secara otomatis tanpa reload!

---

### Skenario 2: Demo 2 Komputer Fisik Berbeda dalam 1 Jaringan (LAN / Wi-Fi)

#### Pada Komputer Server (Komputer A):
1. Cari alamat IP lokal Komputer A (misal melalui `ipconfig` di Windows):
   - Contoh IP: `192.168.1.50`
2. Pastikan port `50051` diizinkan pada Windows Firewall:
   ```powershell
   # Buka port di firewall jika diblokir
   netsh advfirewall firewall add rule name="gRPC Cinema" dir=in action=allow protocol=TCP localport=50051
   ```
3. Jalankan server dengan bind ke `0.0.0.0`:
   ```powershell
   python -m server.main --host 0.0.0.0 --port 50051
   ```

#### Pada Komputer Client (Komputer B):
1. Buka terminal pada komputer kedua (pastikan terhubung ke Wi-Fi / switch yang sama).
2. Jalankan client dengan mengarahkan parameter `--server` ke IP Komputer A:
   ```powershell
   python -m client.cli --server 192.168.1.50:50051
   ```
3. Lakukan pemesanan kursi atau buka live monitor untuk melihat pembaruan data secara real-time antar-mesin.

---

### Skenario 3: Demo pada 2 atau Multi-Container (Docker & Docker Compose)

Telah disediakan file `Dockerfile.server`, `Dockerfile.client`, dan `docker-compose.yml`.

1. Jalankan seluruh container menggunakan Docker Compose:
   ```bash
   docker compose up --build
   ```
   Langkah ini akan membuat:
   - Container `cinema-server` (menjalankan server gRPC port 50051).
   - Container `cinema-client1` (Client node 1).
   - Container `cinema-client2` (Client node 2).
   - Network bridge terisolasi `cinema-net`.

2. Untuk berinteraksi dengan salah satu client container:
   ```bash
   docker attach cinema-client1
   ```
   *(Atau jalankan terminal interaktif baru: `docker exec -it cinema-client1 python -m client.cli --server cinema-server:50051`)*

3. Untuk menghentikan container:
   ```bash
   docker compose down
   ```

---

### Skenario 4: Menjalankan Skrip Demonstrasi Otomatis

Tersedia dua skrip otomasi yang dapat dijalankan langsung:

#### 1. Demo Simulasi Gangguan & Toleransi Kegagalan
```powershell
python scripts/demo_fault_tolerance.py localhost:50051
```
Menjalankan otomatis 3 skenario:
- **Skenario 1**: Pengujian timeout & idempotent retry (Request ID sama).
- **Skenario 2**: Pengujian perebutan kursi bersamaan oleh 5 client (hanya 1 pemenang, 4 ditolak tanpa korupsi data).
- **Skenario 3**: Pengujian pembaruan status real-time dengan server streaming.

#### 2. Demo Interaksi Koordinasi 2 Node Client
```powershell
python scripts/run_two_clients_demo.py localhost:50051
```
Memverifikasi integrasi dua client independen yang berkoordinasi dengan server.

#### 3. Pengujian Kinerja (Benchmark Latensi & Throughput)
```powershell
python scripts/benchmark.py localhost:50051
```
Mengukur performa untuk 1, 5, dan 20 client secara bersamaan.

---

## 4. Hasil Verifikasi Pengujian

### Hasil Uji Otomatis Unit & Integrasi
Dijalankan menggunakan `python -m unittest discover -s tests -v`:
- **22 Unit & Integration Tests LULUS 100%** (durasi ~1.3 detik).
- Mencakup:
  - Validasi protobuf contracts.
  - Concurrency & mutex race condition test (10 client bersamaan).
  - Snapshot & update streaming pub/sub verification.
  - At-most-once deduplication & recovery setelah server restart.
  - Client retry logic dengan request_id identik pada simulasi `DEADLINE_EXCEEDED`.

### Hasil Pengukuran Kinerja (Benchmark Empiris)
Pengukuran 200 RPC per tingkat konkurensi:

| Tingkat Konkurensi | Waktu Total (detik) | Throughput (req/detik) | Latensi Rata-rata (ms) |
|---|---|---|---|
| **1 Client** | 1.102 s | 181.4 req/s | 5.03 ms |
| **5 Client** | 0.580 s | 344.9 req/s | 13.90 ms |
| **20 Client** | 0.591 s | 338.3 req/s | 52.25 ms |

**Analisis Kinerja**:
- Peningkatan konkurensi dari 1 ke 5 client meningkatkan throughput secara signifikan (+90%) karena utilisasi ThreadPool server meningkat.
- Pada 20 client bersamaan, throughput stabil di kisaran ~340 req/detik sementara latensi rata-rata meningkat menjadi ~52 ms karena antrean pengerjaan thread pool dan lock serialisasi database SQLite.
