"""Fault injection khusus demo lokal; server utama tidak memakai delay ini."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import time

import grpc

from generated import cinema_pb2_grpc as pb_grpc
from server.database import Database
from server.seat_manager import SeatManager
from server.service import CinemaService


class DelayedBookingService(CinemaService):
    def __init__(self, manager):
        super().__init__(manager)
        self.delay_lock = threading.Lock()
        self.first = True

    def BookSeat(self, request, context):
        response = super().BookSeat(request, context)
        with self.delay_lock:
            delay = self.first
            self.first = False
        # Transaksi sudah commit. Deadline client habis sebelum balasan pertama.
        if delay:
            time.sleep(0.6)
        return response


@contextmanager
def local_demo_server():
    with tempfile.TemporaryDirectory(prefix="cinema-demo-") as directory:
        database = Database(Path(directory) / "demo.db")
        database.initialize()
        manager = SeatManager(database)
        server = grpc.server(ThreadPoolExecutor(max_workers=8))
        pb_grpc.add_CinemaServiceServicer_to_server(DelayedBookingService(manager), server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            yield f"127.0.0.1:{port}", manager
        finally:
            manager.close()
            server.stop(1).wait()
