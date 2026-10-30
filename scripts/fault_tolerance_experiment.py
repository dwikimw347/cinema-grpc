"""Run deterministic local gRPC failure and recovery scenarios, recording evidence."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import uuid

import grpc

from generated import cinema_pb2 as pb
from generated import cinema_pb2_grpc as pb_grpc
from scripts.demo_support import DelayedBookingService
from server.database import Database
from server.main import create_server
from server.seat_manager import SeatManager
from server.service import CinemaService


class GatedBookingService(CinemaService):
    """Expose a deterministic point after SQLite commit and before the response."""

    def __init__(self, manager, committed, release):
        super().__init__(manager)
        self.committed = committed
        self.release = release
        self.first_lock = threading.Lock()
        self.first = True

    def BookSeat(self, request, context):
        response = super().BookSeat(request, context)
        with self.first_lock:
            delay_first_response = self.first
            self.first = False
        if delay_first_response:
            self.committed.set()
            self.release.wait(timeout=5)
        return response


def start_server(db_path, address="127.0.0.1:0", service_factory=None):
    database = Database(db_path)
    database.initialize()
    manager = SeatManager(database)
    from concurrent.futures import ThreadPoolExecutor as Pool
    server = grpc.server(Pool(max_workers=8))
    service = service_factory(manager) if service_factory else CinemaService(manager)
    pb_grpc.add_CinemaServiceServicer_to_server(service, server)
    port = server.add_insecure_port(address)
    if not port:
        raise RuntimeError(f"Tidak dapat membuka {address}")
    server.start()
    return server, manager, f"127.0.0.1:{port}"


def stop_server(server, manager):
    manager.close()
    server.stop(grace=1).wait()


def count_bookings(manager):
    with manager.database.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]


def scenario(results, name, precondition, steps, expected, action):
    try:
        actual = action()
        passed = actual == expected if not isinstance(expected, dict) else all(actual.get(k) == v for k, v in expected.items())
        results.append({"scenario": name, "precondition": precondition, "steps": steps,
                        "expected": expected, "actual": actual,
                        "status": "PASS" if passed else "FAIL", "evidence": str(actual)})
        print(f"[{results[-1]['status']}] {name}: {actual}")
    except Exception as error:
        results.append({"scenario": name, "precondition": precondition, "steps": steps,
                        "expected": expected, "actual": f"{type(error).__name__}: {error}",
                        "status": "FAIL", "evidence": repr(error)})
        print(f"[FAIL] {name}: {error}")


def run_fault_tolerance(output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="cinema-fault-") as temporary:
        db_path = Path(temporary) / "fault.db"
        server, manager, address = start_server(db_path)
        channel = grpc.insecure_channel(address)
        stub = pb_grpc.CinemaServiceStub(channel)

        def unavailable_and_recover():
            nonlocal server, manager, channel, stub
            request = pb.BookSeatRequest(request_id=str(uuid.uuid4()), schedule_id="SCH001",
                                         seat_id="SCH001-A1", customer_name="Recovery")
            stop_server(server, manager)
            channel.close()
            time.sleep(0.1)
            channel = grpc.insecure_channel(address)
            stub = pb_grpc.CinemaServiceStub(channel)
            try:
                stub.BookSeat(request, timeout=3)
                raise AssertionError("Expected UNAVAILABLE while server was stopped")
            except grpc.RpcError as error:
                unavailable = error.code().name
            server, manager, _ = start_server(db_path, address)
            channel.close()
            channel = grpc.insecure_channel(address)
            stub = pb_grpc.CinemaServiceStub(channel)
            response = stub.BookSeat(request, timeout=3)
            return {"unavailable_status": unavailable, "recovered_success": response.success,
                    "booking_rows": count_bookings(manager), "request_id_reused": response.request_id == request.request_id}

        scenario(results, "server unavailable lalu pulih", "Database bersih; server dihentikan sebelum BookSeat.",
                 "Kirim RPC saat mati; mulai kembali pada alamat dan DB yang sama; ulangi UUID yang sama.",
                 {"unavailable_status": "UNAVAILABLE", "recovered_success": True,
                  "booking_rows": 1, "request_id_reused": True}, unavailable_and_recover)

        def invalid_request():
            try:
                stub.BookSeat(pb.BookSeatRequest(request_id="not-a-uuid", schedule_id="SCH001",
                                                 seat_id="SCH001-A2", customer_name="Invalid"), timeout=2)
            except grpc.RpcError as error:
                return error.code().name
            raise AssertionError("Request invalid diterima")

        scenario(results, "validasi request tidak valid", "Server aktif; request_id bukan UUID.",
                 "Kirim BookSeat dengan request_id invalid dan periksa status gRPC.",
                 "INVALID_ARGUMENT", invalid_request)

        def idempotency_conflict():
            request_id = str(uuid.uuid4())
            original = pb.BookSeatRequest(request_id=request_id, schedule_id="SCH001",
                                          seat_id="SCH001-A2", customer_name="Sama")
            first = stub.BookSeat(original, timeout=3)
            altered = pb.BookSeatRequest(request_id=request_id, schedule_id="SCH001",
                                         seat_id="SCH001-A3", customer_name="Berbeda")
            try:
                stub.BookSeat(altered, timeout=3)
            except grpc.RpcError as error:
                return {"first_success": first.success, "conflict_status": error.code().name,
                        "booking_rows": count_bookings(manager)}
            raise AssertionError("Payload berbeda tidak ditolak")

        scenario(results, "konflik payload idempotency", "Server aktif; request_id belum pernah dipakai.",
                 "Booking A2; kirim ulang request_id sama dengan payload A3.",
                 {"first_success": True, "conflict_status": "ALREADY_EXISTS", "booking_rows": 2},
                 idempotency_conflict)

        def reconnect_stream():
            first_channel = grpc.insecure_channel(address)
            first_stub = pb_grpc.CinemaServiceStub(first_channel)
            stream = first_stub.StreamSeatStatus(pb.StreamSeatStatusRequest(schedule_id="SCH001"), timeout=5)
            initial = next(stream)
            stream.cancel()
            first_channel.close()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with manager.lock:
                    if not manager.subscribers:
                        break
                time.sleep(0.01)
            resumed = next(stub.StreamSeatStatus(pb.StreamSeatStatusRequest(schedule_id="SCH001"), timeout=5))
            return {"initial_event": pb.SeatEventType.Name(initial.event_type),
                    "reconnected_event": pb.SeatEventType.Name(resumed.event_type),
                    "snapshot_seats": len(resumed.seats),
                    "booked_seats": sum(seat.status == pb.BOOKED for seat in resumed.seats)}

        scenario(results, "stream disconnect dan subscribe ulang", "Server aktif; dua booking selesai.",
                 "Terima SNAPSHOT, batalkan stream, buka stream baru dan periksa SNAPSHOT terbaru.",
                 {"initial_event": "SNAPSHOT", "reconnected_event": "SNAPSHOT",
                  "snapshot_seats": 20, "booked_seats": 2}, reconnect_stream)
        channel.close()
        stop_server(server, manager)

    with tempfile.TemporaryDirectory(prefix="cinema-timeout-") as temporary:
        db_path = Path(temporary) / "timeout.db"
        committed = threading.Event()
        release = threading.Event()
        server, manager, address = start_server(
            db_path, service_factory=lambda current: GatedBookingService(current, committed, release))
        channel = grpc.insecure_channel(address)
        stub = pb_grpc.CinemaServiceStub(channel)
        stream = stub.StreamSeatStatus(pb.StreamSeatStatusRequest(schedule_id="SCH001"), timeout=5)
        snapshot = next(stream)
        request = pb.BookSeatRequest(request_id=str(uuid.uuid4()), schedule_id="SCH001",
                                     seat_id="SCH001-A1", customer_name="Commit then timeout")

        def commit_timeout_retry():
            def first_call():
                return stub.BookSeat(request, timeout=0.15)
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(first_call)
                if not committed.wait(timeout=3):
                    raise AssertionError("Commit hook tidak tercapai")
                with manager.database.connect() as connection:
                    committed_booking_id = connection.execute(
                        "SELECT booking_id FROM bookings WHERE request_id = ?", (request.request_id,)
                    ).fetchone()[0]
                try:
                    pending.result(timeout=2)
                    raise AssertionError("Expected DEADLINE_EXCEEDED")
                except grpc.RpcError as error:
                    first_status = error.code().name
            retry = stub.BookSeat(request, timeout=3)
            update = next(stream)
            subscriber = next(iter(manager.subscribers))
            duplicate_event_published = not subscriber.updates.empty()
            release.set()
            stream.cancel()
            return {"first_status": first_status, "retry_success": retry.success,
                    "retry_booking_id": retry.booking_id, "db_booking_rows": count_bookings(manager),
                    "same_request_id": retry.request_id == request.request_id,
                    "same_booking_id_as_committed_record": retry.booking_id == committed_booking_id,
                    "initial_stream_snapshot": pb.SeatEventType.Name(snapshot.event_type),
                    "single_booking_update": update.event_type == pb.UPDATE and len(update.seats) == 1,
                    "duplicate_event_published": duplicate_event_published}

        scenario(results, "timeout deterministik setelah commit dan retry", "DB bersih; response pertama ditahan oleh hook setelah service commit.",
                 "Tunggu sinyal post-commit; biarkan 150 ms deadline kedaluwarsa; ulangi request_id dan payload.",
                 {"first_status": "DEADLINE_EXCEEDED", "retry_success": True,
                  "db_booking_rows": 1, "same_request_id": True,
                  "same_booking_id_as_committed_record": True,
                  "initial_stream_snapshot": "SNAPSHOT", "single_booking_update": True,
                  "duplicate_event_published": False}, commit_timeout_retry)
        release.set()
        channel.close()
        stop_server(server, manager)

    with tempfile.TemporaryDirectory(prefix="cinema-timeout-before-") as temporary:
        db_path = Path(temporary) / "before.db"
        release = threading.Event()

        class PreCommitDelayService(CinemaService):
            def BookSeat(self, request, context):
                if request.seat_id == "SCH001-A1":
                    release.wait(timeout=1)
                return super().BookSeat(request, context)

        server, manager, address = start_server(db_path, service_factory=PreCommitDelayService)
        channel = grpc.insecure_channel(address)
        stub = pb_grpc.CinemaServiceStub(channel)
        request = pb.BookSeatRequest(request_id=str(uuid.uuid4()), schedule_id="SCH001",
                                     seat_id="SCH001-A1", customer_name="Before commit")

        def timeout_before_commit():
            try:
                stub.BookSeat(request, timeout=0.1)
            except grpc.RpcError as error:
                status = error.code().name
            release.set()
            time.sleep(0.05)
            retried = stub.BookSeat(request, timeout=3)
            return {"first_status": status, "retry_success": retried.success,
                    "db_booking_rows": count_bookings(manager), "same_request_id": retried.request_id == request.request_id}

        scenario(results, "timeout sebelum commit lalu retry setelah koneksi aktif", "DB bersih; handler ditahan sebelum transaksi.",
                 "Deadline 100 ms; lepaskan handler; retry UUID/payload yang sama.",
                 {"first_status": "DEADLINE_EXCEEDED", "retry_success": True,
                  "db_booking_rows": 1, "same_request_id": True}, timeout_before_commit)
        channel.close()
        stop_server(server, manager)

    document = {
        "environment": "Local loopback gRPC; isolated temporary SQLite databases; Python runtime and gRPC installed in current environment.",
        "scenarios": results,
        "summary": {"passed": sum(row["status"] == "PASS" for row in results),
                    "failed": sum(row["status"] == "FAIL" for row in results)},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/fault_tolerance_results.json"))
    args = parser.parse_args()
    result = run_fault_tolerance(args.output)
    if result["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
