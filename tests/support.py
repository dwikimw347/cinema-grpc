from pathlib import Path
import tempfile
import time
import unittest
import uuid

import grpc

from generated import cinema_pb2 as pb
from generated import cinema_pb2_grpc as pb_grpc
from server.main import create_server


class RpcTestCase(unittest.TestCase):
    def setUp(self):
        # Jangan gunakan data/cinema.db. Semua test punya database sendiri.
        self.temp = tempfile.TemporaryDirectory(prefix="cinema-test-")
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "test.db"
        self.start_server()
        self.addCleanup(self.stop_server)

    def start_server(self):
        self.server, self.manager, port = create_server(self.db_path, "127.0.0.1:0")
        self.port = port
        self.server.start()
        self.channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        try:
            grpc.channel_ready_future(self.channel).result(timeout=5)
        except Exception:
            self.stop_server()
            raise
        self.stub = pb_grpc.CinemaServiceStub(self.channel)

    def stop_server(self):
        self.manager.close()
        self.server.stop(0).wait()
        self.channel.close()

    def request(self, **changes):
        fields = dict(request_id=str(uuid.uuid4()), schedule_id="SCH001",
                      seat_id="SCH001-A1", customer_name="Mahasiswa")
        fields.update(changes)
        return pb.BookSeatRequest(**fields)

    def booking_count(self, confirmed_only=False):
        with self.manager.database.connect() as connection:
            if confirmed_only:
                return connection.execute(
                    "SELECT COUNT(*) FROM bookings WHERE booking_status = ?", ("CONFIRMED",)
                ).fetchone()[0]
            return connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]

    def wait_subscribers(self, count):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.manager.lock:
                if len(self.manager.subscribers) == count:
                    return
            time.sleep(0.01)
        self.fail(f"Jumlah subscriber tidak menjadi {count}")
