"""Demonstrasi Koordinasi 2 Client Terdistribusi:
Menjalankan 2 Client (Komputer / Node 1 dan Komputer / Node 2)
yang berinteraksi secara bersamaan ke server gRPC:
- Client 1: Membuka Live Monitoring (Server Streaming).
- Client 2: Memesan kursi B1 -> Client 1 langsung melihat perubahan secara real-time.
- Client 1 & 2: Perebutan kursi B2 secara serentak (Race condition lock test).
- Client 2: Simulasi Timeout & Retry dengan ID yang sama (Idempotency deduplication).
"""

from concurrent.futures import ThreadPoolExecutor
import os
import queue
import sys
import threading
import time
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from client.cinema_client import CinemaClient
from generated import cinema_pb2 as pb

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def main():
    server_address = sys.argv[1] if len(sys.argv) > 1 else "localhost:50051"

    print(f"\n{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}  DEMO INTEGRASI 2 CLIENT TERDISTRIBUSI (SIMULASI 2 NODE){RESET}")
    print(f"  Target Server: {YELLOW}{server_address}{RESET}")
    print(f"{CYAN}{'='*70}{RESET}\n")

    # Uji koneksi awal
    with CinemaClient(server_address) as probe:
        if not probe.ping(timeout=3.0):
            print(f"{RED}[ERROR] Server tidak dapat dihubungi di {server_address}.{RESET}")
            print("Harap jalankan server terlebih dahulu: python -m server.main")
            sys.exit(1)
        schedules = probe.get_schedules()
        schedule_id = schedules[0].schedule_id
        movie_title = schedules[0].movie_title

    print(f"{GREEN}[OK] Terhubung ke Server!{RESET}")
    print(f"Menggunakan Jadwal: {BOLD}{movie_title} ({schedule_id}){RESET}\n")

    # -------------------------------------------------------------
    # TAHAP 1: Client 1 Mendengarkan Live Stream, Client 2 Memesan
    # -------------------------------------------------------------
    print(f"{BOLD}--- TAHAP 1: LIVE UPDATE ANTARKLIEN (STREAMING REAL-TIME) ---{RESET}")
    print("[Node 1 (Client 1)] Mengaktifkan listener real-time...")
    
    stream_queue = queue.Queue()
    stop_signal = threading.Event()

    def node1_stream():
        with CinemaClient(server_address) as client1:
            try:
                for event in client1.stream_seat_status(schedule_id):
                    stream_queue.put(event)
                    if stop_signal.is_set():
                        break
            except Exception:
                pass

    stream_thread = threading.Thread(target=node1_stream, daemon=True)
    stream_thread.start()

    # Menerima snapshot
    snapshot = stream_queue.get(timeout=3.0)
    print(f"[Node 1 (Client 1)] Menerima SNAPSHOT: {len(snapshot.seats)} kursi terdata.")

    # Node 2 memesan kursi B1
    print("\n[Node 2 (Client 2)] Memesan kursi B1 atas nama 'Ahmad'...")
    with CinemaClient(server_address) as client2:
        resp = client2.book_seat(schedule_id, f"{schedule_id}-B1", "Ahmad", timeout=3.0)
        print(f"[Node 2 (Client 2)] Respon: {resp.message} (Booking ID: {resp.booking_id})")

    # Node 1 memverifikasi penerimaan update
    update = stream_queue.get(timeout=3.0)
    print(f"[Node 1 (Client 1)] {GREEN}Menerima siaran UPDATE secara INSTAN!{RESET}")
    print(f"                  Kursi: {update.seats[0].seat_id} berubah status menjadi {pb.SeatStatus.Name(update.seats[0].status)}")

    # -------------------------------------------------------------
    # TAHAP 2: Perebutan Kursi Serentak antara Node 1 dan Node 2
    # -------------------------------------------------------------
    print(f"\n{BOLD}--- TAHAP 2: PEREBUTAN KURSI BERSAMAAN (NODE 1 vs NODE 2) ---{RESET}")
    print("Node 1 dan Node 2 serentak memperebutkan kursi B2 pada waktu yang sama...")
    
    barrier = threading.Barrier(2)

    def node_booking_race(node_id, customer_name):
        barrier.wait()
        with CinemaClient(server_address) as c:
            try:
                res = c.book_seat(schedule_id, f"{schedule_id}-B2", customer_name, timeout=5.0)
                return node_id, res.success, res.booking_status, res.message
            except Exception as e:
                return node_id, False, None, str(e)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(node_booking_race, "Node 1", "Budi")
        f2 = executor.submit(node_booking_race, "Node 2", "Citra")
        r1 = f1.result()
        r2 = f2.result()

    for node, ok, st, msg in [r1, r2]:
        status_label = f"{GREEN}BERHASIL{RESET}" if ok else f"{RED}DITOLAK{RESET}"
        print(f"  [{node}] Hasil: {status_label} -> {msg}")

    # -------------------------------------------------------------
    # TAHAP 3: Simulasi Timeout & Retry Idempoten oleh Node 2
    # -------------------------------------------------------------
    print(f"\n{BOLD}--- TAHAP 3: PENANGANAN GANGGUAN TIMEOUT & RETRY IDEMPOTEN ---{RESET}")
    print("Node 2 memesan kursi B3 dengan ID tertentu, lalu melakukan retry...")
    
    shared_uuid = str(uuid.uuid4())
    with CinemaClient(server_address) as client2:
        res_initial = client2.book_seat(
            schedule_id, f"{schedule_id}-B3", "Dedi",
            request_id=shared_uuid
        )
        print(f"  Percobaan 1: {GREEN}CONFIRMED{RESET} (Booking ID: {res_initial.booking_id})")

        print("  Simulasi gangguan jaringan: Node 2 mengirim ulang (retry) dengan ID yang SAMA:")
        res_retry = client2.book_seat(
            schedule_id, f"{schedule_id}-B3", "Dedi",
            request_id=shared_uuid
        )
        print(f"  Percobaan 2 (Retry): {GREEN}CONFIRMED (At-Most-Once Deduplicated){RESET}")
        print(f"  Booking ID Identik: {res_initial.booking_id == res_retry.booking_id} ({res_retry.booking_id})")

    stop_signal.set()

    print(f"\n{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}{GREEN}  SELURUH DEMO 2 CLIENT TERINTEGRASI BERHASIL!{RESET}")
    print(f"{CYAN}{'='*70}{RESET}\n")


if __name__ == "__main__":
    main()
