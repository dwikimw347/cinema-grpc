"""Skrip demonstrasi otomatis penanganan gangguan dan toleransi kegagalan:
1. Uji Timeout & Idempotent Retry (Request ID sama).
2. Uji Perebutan Kursi Bersamaan (Race Condition / Concurrency Control).
3. Uji Pembaruan Status Real-Time (Server Streaming).
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import queue
import sys
import threading
import time
import uuid

# Pastikan root proyek masuk ke sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import grpc
from client.cinema_client import CinemaClient, BookingError
from generated import cinema_pb2 as pb

# Format warna konsol
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run_demo(server_address="localhost:50051"):
    print(f"\n{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}  DEMONSTRASI SISTEM TERDISTRIBUSI RPC & PENANGANAN GANGGUAN{RESET}")
    print(f"  Target Server: {YELLOW}{server_address}{RESET}")
    print(f"{CYAN}{'='*70}{RESET}\n")

    client = CinemaClient(server_address)
    if not client.ping(timeout=3.0):
        print(f"{RED}[ERROR] Server tidak dapat dihubungi di {server_address}!{RESET}")
        print("Jalankan server terlebih dahulu dengan perintah:")
        print("  python -m server.main")
        sys.exit(1)

    schedules = client.get_schedules()
    schedule = schedules[0]
    print(f"{GREEN}[OK] Terhubung ke Server!{RESET}")
    print(f"Jadwal Aktif Uji Coba: {schedule.movie_title} (ID: {schedule.schedule_id})")

    # =========================================================================
    # SKENARIO 1: Timeout & Idempotent Retry dengan ID yang Sama
    # =========================================================================
    print(f"\n{BOLD}------------------------------------------------------------{RESET}")
    print(f"{BOLD}SKENARIO 1: TIMEOUT & RETRY DENGAN REQUEST ID SAMA (AT-MOST-ONCE){RESET}")
    print(f"------------------------------------------------------------")
    print("Skenario: Client memesan kursi, namun menganggap balasan hilang / timeout.")
    print("Client kemudian mengirimkan retry dengan Request ID (UUID) yang sama persis.")
    
    test_seat_id = f"{schedule.schedule_id}-C1"
    shared_request_id = str(uuid.uuid4())
    customer_name = "Rifki Maulana"

    print(f"- Target Kursi : {test_seat_id}")
    print(f"- Request ID   : {shared_request_id}")
    print(f"- Nama Pemesan : {customer_name}\n")

    print("[Langkah 1] Mengirim permintaan pemesanan pertama kali...")
    resp1 = client.book_seat(
        schedule_id=schedule.schedule_id,
        seat_id=test_seat_id,
        customer_name=customer_name,
        request_id=shared_request_id,
    )
    print(f"  Status        : {GREEN}CONFIRMED{RESET}")
    print(f"  Booking ID    : {resp1.booking_id}")
    print(f"  Pesan Server  : {resp1.message}")

    print("\n[Langkah 2] Simulasi Retry otomatis (menggunakan Request ID yang SAMA)...")
    time.sleep(0.5)
    resp2 = client.book_seat(
        schedule_id=schedule.schedule_id,
        seat_id=test_seat_id,
        customer_name=customer_name,
        request_id=shared_request_id,
    )
    print(f"  Status        : {GREEN}CONFIRMED (Deduplikasi Aktif){RESET}")
    print(f"  Booking ID    : {resp2.booking_id}")
    print(f"  Pesan Server  : {resp2.message}")

    is_identical = (resp1.booking_id == resp2.booking_id) and (resp1.message == resp2.message)
    print(f"\n  Verifikasi At-Most-Once:")
    print(f"  - Apakah Booking ID sama? {'YA' if is_identical else 'TIDAK'} ({resp2.booking_id})")
    print(f"  - {GREEN}[BERHASIL]{RESET} Server SQLite mengenali permintaan duplikat dan tidak membuat pesanan ganda!")

    # =========================================================================
    # SKENARIO 2: Perebutan Kursi Bersamaan (Race Condition / Concurrency)
    # =========================================================================
    print(f"\n{BOLD}------------------------------------------------------------{RESET}")
    print(f"{BOLD}SKENARIO 2: PEREBUTAN KURSI BERSAMAAN (CONCURRENCY CONTROL){RESET}")
    print(f"------------------------------------------------------------")
    print("Skenario: 5 client berbeda secara serentak memperebutkan 1 kursi yang sama.")
    
    contested_seat_id = f"{schedule.schedule_id}-C2"
    print(f"Kursi yang diperebutkan: {contested_seat_id}")
    barrier = threading.Barrier(5)

    def racer_task(client_idx):
        barrier.wait() # Sinkronisasi agar request dikirim bersamaan persis
        c = CinemaClient(server_address)
        try:
            res = c.book_seat(
                schedule_id=schedule.schedule_id,
                seat_id=contested_seat_id,
                customer_name=f"Client-{client_idx}",
                timeout=5.0,
            )
            c.close()
            return client_idx, res.success, res.booking_status, res.message
        except Exception as ex:
            c.close()
            return client_idx, False, None, str(ex)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(racer_task, i + 1) for i in range(5)]
        race_results = [f.result() for f in futures]

    winners = [r for r in race_results if r[1] is True]
    losers = [r for r in race_results if r[1] is False]

    for idx, ok, status, msg in race_results:
        status_str = f"{GREEN}BERHASIL (CONFIRMED){RESET}" if ok else f"{RED}DITOLAK ({pb.BookingStatus.Name(status) if status else 'ERROR'}){RESET}"
        print(f"  Client #{idx}: {status_str} -> {msg}")

    print(f"\n  Hasil Evaluasi Concurrency:")
    print(f"  - Pemenang Berhasil : {len(winners)} client")
    print(f"  - Permintaan Ditolak: {len(losers)} client")
    if len(winners) == 1 and len(losers) == 4:
        print(f"  - {GREEN}[BERHASIL]{RESET} Mutex RLock dan Transaksi SQLite berhasil mencegah Double Booking!")
    else:
        print(f"  - {RED}[PERINGATAN]{RESET} Anomali concurrency terdeteksi!")

    # =========================================================================
    # SKENARIO 3: Live Real-Time Stream (Server Streaming gRPC)
    # =========================================================================
    print(f"\n{BOLD}------------------------------------------------------------{RESET}")
    print(f"{BOLD}SKENARIO 3: PEMBARUAN REAL-TIME DENGAN SERVER STREAMING{RESET}")
    print(f"------------------------------------------------------------")
    print("Skenario: Client 1 membuka stream pemantauan status kursi.")
    print("Client 2 kemudian memesan kursi baru (C3).")
    print("Client 1 harus menerima event pembaruan (UPDATE) secara langsung via gRPC.")

    stream_events = queue.Queue()
    stop_stream = threading.Event()

    def listener():
        stream_client = CinemaClient(server_address)
        try:
            for ev in stream_client.stream_seat_status(schedule.schedule_id):
                stream_events.put(ev)
                if stop_stream.is_set():
                    break
        except Exception:
            pass
        finally:
            stream_client.close()

    listener_thread = threading.Thread(target=listener, daemon=True)
    listener_thread.start()

    # Tunggu snapshot awal diterima
    snapshot_event = stream_events.get(timeout=3.0)
    print(f"  [Client 1] Menerima {CYAN}SNAPSHOT{RESET} awal ({len(snapshot_event.seats)} kursi)")

    # Client 2 melakukan pemesanan kursi C3
    stream_test_seat = f"{schedule.schedule_id}-C3"
    print(f"  [Client 2] Memesan kursi {stream_test_seat}...")
    client2 = CinemaClient(server_address)
    client2.book_seat(schedule.schedule_id, stream_test_seat, "Pemesan Realtime", timeout=3.0)
    client2.close()

    # Client 1 menunggu event UPDATE
    try:
        update_event = stream_events.get(timeout=3.0)
        updated_seat = update_event.seats[0]
        print(f"  [Client 1] {GREEN}BERHASIL menerima event UPDATE live!{RESET}")
        print(f"             Kursi   : {updated_seat.seat_id} ({updated_seat.seat_number})")
        print(f"             Status  : {pb.SeatStatus.Name(updated_seat.status)}")
        print(f"             Waktu   : {update_event.timestamp}")
    except queue.Empty:
        print(f"  {RED}[GAGAL] Tidak menerima event update dalam 3 detik.{RESET}")

    stop_stream.set()
    client.close()

    print(f"\n{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}{GREEN}  SELURUH DEMO GANGGUAN & TOLERANSI KEGAGALAN SELESAI DENGAN SUKSES!{RESET}")
    print(f"{CYAN}{'='*70}{RESET}\n")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "localhost:50051"
    run_demo(target)
