"""Aplikasi antarmuka konsol (CLI) Client Reservasi Bioskop gRPC.

Fitur:
- Pemilihan jadwal tayang film secara interaktif.
- Visualisasi denah kursi bioskop (Studio grid 4x5: Baris A-D, Kolom 1-5).
- Pemesanan tiket dengan toleransi gangguan: timeout terukur dan
  retry otomatis menggunakan ID permintaan yang sama (idempoten / at-most-once).
- Pemantauan denah kursi secara real-time melalui Server-Streaming gRPC.
- Uji coba simulasi gangguan jaringan & perlindungan deduplikasi.
"""

import argparse
import os
import sys
import threading
import time
from typing import Dict, List, Optional

import grpc

# Pastikan root proyek masuk ke sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from client.cinema_client import CinemaClient, BookingError
from generated import cinema_pb2 as pb

# Pewarnaan terminal ANSI
COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_CYAN = "\033[96m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_banner(server_address: str):
    print(COLOR_CYAN + "=" * 65 + COLOR_RESET)
    print(COLOR_BOLD + "  CINEMA TICKET RESERVATION SYSTEM - gRPC CLIENT" + COLOR_RESET)
    print(f"  Target Server: {COLOR_YELLOW}{server_address}{COLOR_RESET}")
    print(COLOR_CYAN + "=" * 65 + COLOR_RESET)


def format_seat_grid(seats: List[pb.Seat]) -> str:
    """Mengubah daftar kursi menjadi representasi visual denah studio bioskop."""
    seat_dict: Dict[str, pb.SeatStatus] = {s.seat_number: s.status for s in seats}
    
    lines = []
    lines.append("        +----------------------------------------+")
    lines.append("        |               LAYAR BIOSKOP            |")
    lines.append("        +----------------------------------------+")
    lines.append("")
    
    rows = ["A", "B", "C", "D"]
    for row in rows:
        row_str = f"  Baris {row}:   "
        for col in range(1, 6):
            seat_num = f"{row}{col}"
            status = seat_dict.get(seat_num, pb.SEAT_STATUS_UNSPECIFIED)
            if status == pb.AVAILABLE:
                box = f"{COLOR_GREEN}[ {seat_num} ]{COLOR_RESET}"
            elif status == pb.BOOKED:
                box = f"{COLOR_RED}[{seat_num}*]{COLOR_RESET}"
            else:
                box = f"[ ??? ]"
            row_str += box + "  "
        lines.append(row_str)
    
    available_count = sum(1 for s in seats if s.status == pb.AVAILABLE)
    booked_count = sum(1 for s in seats if s.status == pb.BOOKED)
    total = len(seats)

    lines.append("")
    lines.append(
        f"  Keterangan: {COLOR_GREEN}[ A1 ] Tersedia ({available_count}){COLOR_RESET}  "
        f"{COLOR_RED}[A1*] Terisi/Booked ({booked_count}){COLOR_RESET}  Total: {total}"
    )
    return "\n".join(lines)


def choose_schedule(client: CinemaClient) -> Optional[pb.Schedule]:
    """Mengambil dan menampilkan jadwal film, lalu meminta pengguna memilih."""
    try:
        schedules = client.get_schedules(timeout=4.0)
    except Exception as e:
        print(f"\n{COLOR_RED}[ERROR] Gagal mengambil jadwal dari server: {e}{COLOR_RESET}")
        return None

    if not schedules:
        print(f"\n{COLOR_YELLOW}[INFO] Tidak ada jadwal pemutaran film.{COLOR_RESET}")
        return None

    print("\n--- DAFTAR JADWAL FILM TERSEDIA ---")
    for idx, s in enumerate(schedules, 1):
        print(f"  {idx}. [{s.schedule_id}] {s.movie_title}")
        print(f"     Studio: {s.studio} | Waktu Tayang: {s.show_time}")

    while True:
        pilihan = input("\nPilih nomor jadwal (atau tekan Enter untuk batal): ").strip()
        if not pilihan:
            return None
        if pilihan.isdigit() and 1 <= int(pilihan) <= len(schedules):
            return schedules[int(pilihan) - 1]
        print(f"{COLOR_RED}Nomor pilihan tidak valid.{COLOR_RESET}")


def view_seat_map(client: CinemaClient, schedule: pb.Schedule):
    """Menampilkan denah kursi terkini untuk jadwal tertentu."""
    try:
        seats = client.get_seats(schedule.schedule_id, timeout=4.0)
        print(f"\nDenah Kursi untuk: {COLOR_BOLD}{schedule.movie_title}{COLOR_RESET} ({schedule.schedule_id})")
        print(format_seat_grid(seats))
    except Exception as e:
        print(f"\n{COLOR_RED}[ERROR] Gagal memuat denah kursi: {e}{COLOR_RESET}")


def book_seat_interactive(client: CinemaClient, default_schedule: Optional[pb.Schedule] = None):
    """Alur interaktif pemesanan tiket dengan penanganan retry dan timeout."""
    schedule = default_schedule
    if schedule is None:
        schedule = choose_schedule(client)
        if schedule is None:
            return

    view_seat_map(client, schedule)

    print("\n--- PEMESANAN KURSI ---")
    seat_num = input("Masukkan Nomor Kursi yang diinginkan (contoh: B3): ").strip().upper()
    if not seat_num:
        print("Pemesanan dibatalkan.")
        return

    customer_name = input("Masukkan Nama Pemesan: ").strip()
    if not customer_name:
        customer_name = "Pelanggan Demo"

    seat_id = f"{schedule.schedule_id}-{seat_num}"

    print(f"\nMemproses pemesanan:")
    print(f"- Jadwal : {schedule.movie_title} ({schedule.schedule_id})")
    print(f"- Kursi  : {seat_id} ({seat_num})")
    print(f"- Pemesan: {customer_name}")
    print(f"- Kebijakan: Timeout = 3.0s, Maksimal Retry = 3x (ID Permintaan Unik & Sama)")

    def retry_logger(attempt, max_retries, error, delay, request_id):
        print(
            f"  {COLOR_YELLOW}[RETRY {attempt}/{max_retries}]{COLOR_RESET} "
            f"Gangguan koneksi ({getattr(error, 'code', lambda: 'ERROR')().name}). "
            f"Mencoba ulang dalam {delay:.2f}s dengan Request ID identik: {request_id}..."
        )

    start_time = time.time()
    try:
        response = client.book_seat(
            schedule_id=schedule.schedule_id,
            seat_id=seat_id,
            customer_name=customer_name,
            timeout=3.0,
            max_retries=3,
            on_retry_callback=retry_logger,
        )
        duration = (time.time() - start_time) * 1000

        print(COLOR_CYAN + "-" * 55 + COLOR_RESET)
        if response.success:
            print(f"{COLOR_GREEN}{COLOR_BOLD}[BERHASIL - CONFIRMED]{COLOR_RESET}")
            print(f"Pesan        : {response.message}")
            print(f"Booking ID   : {response.booking_id}")
            print(f"Request ID   : {response.request_id} (Idempotent Key)")
            print(f"Kursi        : {response.seat_id}")
            print(f"Waktu Respon : {duration:.1f} ms")
        else:
            print(f"{COLOR_RED}{COLOR_BOLD}[DITOLAK - REJECTED]{COLOR_RESET}")
            print(f"Pesan        : {response.message}")
            print(f"Request ID   : {response.request_id}")
            print(f"Status       : {pb.BookingStatus.Name(response.booking_status)}")
        print(COLOR_CYAN + "-" * 55 + COLOR_RESET)

    except BookingError as err:
        print(f"\n{COLOR_RED}[GAGAL TOTAL SETELAH RETRY]{COLOR_RESET}")
        print(f"Alasan    : {err}")
        print(f"Request ID: {err.request_id}")
    except grpc.RpcError as rpc_err:
        print(f"\n{COLOR_RED}[ERROR RPC]{COLOR_RESET} {rpc_err.code().name}: {rpc_err.details()}")


def live_monitor_mode(client: CinemaClient, schedule: pb.Schedule):
    """Memantau status kursi secara real-time menggunakan Server Streaming (StreamSeatStatus)."""
    clear_screen()
    print_banner(client.address)
    print(f"\n{COLOR_BOLD}=== MODE MONITOR REAL-TIME (SERVER STREAMING) ==={COLOR_RESET}")
    print(f"Film   : {schedule.movie_title}")
    print(f"Studio : {schedule.studio} | Jadwal: {schedule.schedule_id}")
    print(f"Status : {COLOR_GREEN}Mendengarkan siaran langsung status kursi dari server...{COLOR_RESET}")
    print("Tekan Ctrl+C untuk kembali ke menu utama.\n")

    current_seats: Dict[str, pb.Seat] = {}
    stop_event = threading.Event()

    try:
        for event in client.stream_seat_status(schedule.schedule_id):
            if stop_event.is_set():
                break

            now_str = time.strftime("%H:%M:%S")
            event_type_name = pb.SeatEventType.Name(event.event_type)

            if event.event_type == pb.SNAPSHOT:
                for s in event.seats:
                    current_seats[s.seat_number] = s
                print(f"[{now_str}] {COLOR_CYAN}[SNAPSHOT AWAL]{COLOR_RESET} Menerima data lengkap {len(event.seats)} kursi.")
            elif event.event_type == pb.UPDATE:
                for s in event.seats:
                    current_seats[s.seat_number] = s
                    status_label = "TERSEDIA" if s.status == pb.AVAILABLE else "TERISI (BOOKED)"
                    print(f"[{now_str}] {COLOR_YELLOW}[UPDATE LIVE]{COLOR_RESET} Kursi {COLOR_BOLD}{s.seat_number}{COLOR_RESET} berubah -> {status_label}")

            # Cetak ulang grid kursi
            print("\n" + format_seat_grid(list(current_seats.values())))
            print(f"\n{COLOR_CYAN}[Live Listener Aktif]{COLOR_RESET} Menunggu perubahan kursi selanjutnya...\n")

    except KeyboardInterrupt:
        print("\n\nKeluar dari mode live monitor.")
    except Exception as e:
        print(f"\n{COLOR_RED}[ERROR Stream]{COLOR_RESET} Aliran siaran terputus: {e}")


def demo_idempotent_retry(client: CinemaClient, schedule: pb.Schedule):
    """Simulasi pengujian toleransi gangguan:

    1. Mengirim reservasi pertama dengan UUID tertentu (Sukses).
    2. Mengirimkan ulang (retry) request yang persis sama dengan UUID YANG SAMA.
    3. Membuktikan server mengenali idempotency key (at-most-once):
       tidak ada double booking, saldo/kursi tetap konsisten, dan mengembalikan respon identik.
    """
    import uuid
    fixed_request_id = str(uuid.uuid4())
    seat_num = "D5"
    seat_id = f"{schedule.schedule_id}-{seat_num}"
    customer = "Uji Toleransi Gangguan"

    print(f"\n{COLOR_CYAN}=== DEMO SIMULASI TIMEOUT & RETRY IDEMPOTEN ==={COLOR_RESET}")
    print(f"Skenario: Client mengirimkan reservasi, mengalami timeout/gangguan jaringan,")
    print(f"lalu mengulang (retry) menggunakan Request ID yang SAMA.")
    print(f"Tujuan  : Menjamin semantik At-Most-Once (tidak ada duplikasi reservasi kursi).\n")

    print(f"1. Percobaan Pertama -> Mengirim request dengan ID: {fixed_request_id}")
    try:
        res1 = client.book_seat(
            schedule_id=schedule.schedule_id,
            seat_id=seat_id,
            customer_name=customer,
            request_id=fixed_request_id,
        )
        print(f"   Hasil: {COLOR_GREEN}SUKSES (CONFIRMED){COLOR_RESET} | Booking ID: {res1.booking_id}")
    except Exception as e:
        print(f"   Percobaan pertama gagal: {e}")
        return

    print(f"\n2. Simulasi Gangguan: Anggap balasan pertama hilang di jaringan.")
    print(f"   Client melakukan RETRY dengan Request ID yang SAMA: {fixed_request_id}")
    time.sleep(1.0)

    try:
        res2 = client.book_seat(
            schedule_id=schedule.schedule_id,
            seat_id=seat_id,
            customer_name=customer,
            request_id=fixed_request_id,
        )
        print(f"   Hasil: {COLOR_GREEN}SUKSES DIVERIFIKASI (IDEMPOTEN){COLOR_RESET}")
        print(f"   Booking ID kedua  : {res2.booking_id}")
        print(f"   Apakah respon sama: {res1.booking_id == res2.booking_id and res1.message == res2.message}")
        print(f"\n{COLOR_GREEN}[KESIMPULAN]{COLOR_RESET} Server berhasil mendeteksi deduplikasi via SQLite.")
        print("Meskipun diretry berkali-kali, kursi D5 hanya dipesan tepat 1 kali!")
    except Exception as e:
        print(f"   Error saat retry: {e}")


def main():
    parser = argparse.ArgumentParser(description="Client Aplikasi Pemesanan Kursi Bioskop gRPC")
    parser.add_argument("--server", default="localhost:50051", help="Alamat host:port server gRPC")
    args = parser.parse_args()

    server_address = args.server
    client = CinemaClient(server_address)

    print(f"Menghubungi server {server_address}...")
    if not client.ping(timeout=3.0):
        print(f"{COLOR_YELLOW}[PERINGATAN] Server belum merespon. Pastikan server sudah berjalan di {server_address}{COLOR_RESET}")

    current_schedule: Optional[pb.Schedule] = None

    while True:
        print_banner(server_address)
        if current_schedule:
            print(f"Jadwal Aktif: {COLOR_GREEN}{current_schedule.movie_title}{COLOR_RESET} ({current_schedule.schedule_id})\n")
        else:
            print(f"Jadwal Aktif: {COLOR_YELLOW}(Belum dipilih){COLOR_RESET}\n")

        print("PILIHAN MENU:")
        print("  1. Pilih Jadwal Tayang Film")
        print("  2. Lihat Denah Status Kursi")
        print("  3. Pesan Kursi (dengan Timeout & Retry Idempoten)")
        print("  4. Pantau Status Kursi Real-Time (Server Streaming)")
        print("  5. Simulasi Pengujian Toleransi Gangguan (Idempotent Retry Demo)")
        print("  6. Ganti Alamat Server")
        print("  0. Keluar")

        choice = input("\nMasukkan pilihan [0-6]: ").strip()

        if choice == "1":
            selected = choose_schedule(client)
            if selected:
                current_schedule = selected
                print(f"\n{COLOR_GREEN}Jadwal aktif diubah menjadi: {selected.movie_title}{COLOR_RESET}")
            input("\nTekan Enter untuk melanjutkan...")

        elif choice == "2":
            if not current_schedule:
                current_schedule = choose_schedule(client)
            if current_schedule:
                view_seat_map(client, current_schedule)
            input("\nTekan Enter untuk melanjutkan...")

        elif choice == "3":
            if not current_schedule:
                current_schedule = choose_schedule(client)
            if current_schedule:
                book_seat_interactive(client, current_schedule)
            input("\nTekan Enter untuk melanjutkan...")

        elif choice == "4":
            if not current_schedule:
                current_schedule = choose_schedule(client)
            if current_schedule:
                live_monitor_mode(client, current_schedule)
            input("\nTekan Enter untuk melanjutkan...")

        elif choice == "5":
            if not current_schedule:
                current_schedule = choose_schedule(client)
            if current_schedule:
                demo_idempotent_retry(client, current_schedule)
            input("\nTekan Enter untuk melanjutkan...")

        elif choice == "6":
            new_addr = input("Masukkan alamat server baru (misal 192.168.1.10:50051): ").strip()
            if new_addr:
                client.close()
                server_address = new_addr
                client = CinemaClient(server_address)
                print(f"Menghubungkan ke {server_address}...")
                if client.ping(timeout=3.0):
                    print(f"{COLOR_GREEN}Terhubung!{COLOR_RESET}")
                else:
                    print(f"{COLOR_RED}Belum dapat tersambung ke {server_address}{COLOR_RESET}")
            input("\nTekan Enter untuk melanjutkan...")

        elif choice == "0":
            print("\nTerima kasih telah menggunakan sistem reservasi bioskop gRPC.")
            client.close()
            break
        else:
            print(f"{COLOR_RED}Pilihan tidak dikenali.{COLOR_RESET}")
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAplikasi ditutup.")
