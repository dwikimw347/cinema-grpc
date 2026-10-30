"""Demo RPC nyata. --local memakai DB sementara dan balasan tertunda setelah commit."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.cinema_client import CinemaClient
from generated import cinema_pb2 as pb


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def demonstrate_deduplication(client, schedule_id, seat_id, require_timeout=False):
    request_id = str(uuid.uuid4())
    retries = []
    def on_retry(attempt, maximum, error, delay, rid):
        retries.append((error.code().name, rid))
        print(f"  Retry {attempt}: {error.code().name}, ID={rid}")

    arguments = dict(schedule_id=schedule_id, seat_id=seat_id,
                     customer_name="Demo deduplikasi", request_id=request_id,
                     timeout=0.15 if require_timeout else 3.0,
                     base_delay=0.01, on_retry_callback=on_retry)
    first = client.book_seat(**arguments)
    require(first.success, f"Booking awal ditolak: {first.message}")
    second = client.book_seat(**arguments)
    require(first == second, "Respons request duplikat tidak identik")
    require(all(rid == request_id for _, rid in retries), "UUID berubah saat retry")
    if require_timeout:
        require(any(code == "DEADLINE_EXCEEDED" for code, _ in retries),
                "Demo belum menghasilkan timeout nyata")
    print(f"  PASS: booking={first.booking_id}, respons ulang identik")
    if not require_timeout:
        print("  Deduplikasi teruji. Uji timeout nyata tersedia melalui --local.")
    return first


def run_demo(server_address, require_timeout=False, racers=5):
    with CinemaClient(server_address) as client:
        require(client.ping(), f"Server tidak dapat dihubungi: {server_address}")
        selected = None
        for schedule in client.get_schedules():
            available = [s for s in client.get_seats(schedule.schedule_id) if s.status == pb.AVAILABLE]
            if len(available) >= 3:
                selected = schedule, available[:3]
                break
        require(selected is not None, "Perlu minimal tiga kursi tersedia; gunakan --local untuk DB demo baru")
        schedule, seats = selected
        print(f"Demo di {server_address}, jadwal {schedule.schedule_id}")
        print("1. Deduplikasi dan retry")
        demonstrate_deduplication(client, schedule.schedule_id, seats[0].seat_id, require_timeout)

        print(f"2. Perebutan satu kursi oleh {racers} client")
        barrier = threading.Barrier(racers)
        def book(index):
            with CinemaClient(server_address) as racer:
                barrier.wait(timeout=5)
                return racer.book_seat(schedule.schedule_id, seats[1].seat_id, f"Client {index}")
        with ThreadPoolExecutor(max_workers=racers) as pool:
            results = list(pool.map(book, range(racers)))
        winners = sum(result.success and result.booking_status == pb.CONFIRMED for result in results)
        rejected = sum(not result.success and result.booking_status == pb.REJECTED for result in results)
        require(winners == 1 and rejected == racers - 1, f"Hasil race tidak sesuai: {winners} berhasil, {rejected} ditolak")
        print(f"  PASS: {winners} CONFIRMED, {rejected} REJECTED")

        print("3. Streaming antar-client")
        with CinemaClient(server_address) as listener:
            stream = listener.stream_seat_status(schedule.schedule_id, timeout=5)
            try:
                snapshot = next(stream)
                require(snapshot.event_type == pb.SNAPSHOT, "Event pertama bukan SNAPSHOT")
                result = client.book_seat(schedule.schedule_id, seats[2].seat_id, "Demo streaming")
                require(result.success, "Booking untuk streaming ditolak")
                # Update pengguna lain bukan bukti booking target demo.
                for event in stream:
                    if event.event_type == pb.UPDATE and any(
                        seat.seat_id == seats[2].seat_id and seat.status == pb.BOOKED
                        for seat in event.seats
                    ):
                        print(f"  PASS: UPDATE BOOKED {seats[2].seat_id}")
                        break
                else:
                    raise RuntimeError("Stream berakhir sebelum update target diterima")
            finally:
                stream.close()
    print("SELURUH SKENARIO YANG DIJALANKAN LULUS")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", nargs="?", default="localhost:50051")
    parser.add_argument("--local", action="store_true", help="Server sementara, DB bersih, timeout nyata setelah commit")
    args = parser.parse_args()
    try:
        if args.local:
            from scripts.demo_support import local_demo_server
            with local_demo_server() as (address, manager):
                return run_demo(address, require_timeout=True)
        return run_demo(args.address)
    except Exception as error:
        print(f"DEMO GAGAL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
