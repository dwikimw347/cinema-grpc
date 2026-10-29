from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import tempfile
import threading
import unittest
import uuid

import grpc

from generated import cinema_pb2 as pb
from server.database import Database
from server.seat_manager import SeatManager
from tests.support import RpcTestCase


class ConcurrencyTests(RpcTestCase):
    def test_ten_concurrent_bookings_exactly_one_winner(self):
        barrier = threading.Barrier(10)

        def book(index):
            request = self.request(customer_name=f"Client {index}")
            barrier.wait(timeout=5)
            return self.stub.BookSeat(request, timeout=10)

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(book, range(10)))
        self.assertEqual(sum(result.success for result in results), 1)
        self.assertEqual(sum(result.booking_status == pb.REJECTED for result in results), 9)
        self.assertEqual(self.booking_count(True), 1)
        self.assertEqual(self.booking_count(), 10)  # Penolakan juga persisten.

    def test_concurrent_duplicate_request(self):
        request = self.request()
        barrier = threading.Barrier(10)

        def book(_):
            barrier.wait(timeout=5)
            return self.stub.BookSeat(request, timeout=10)

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(book, range(10)))
        self.assertTrue(all(result == results[0] and result.success for result in results))
        self.assertEqual(self.booking_count(), 1)

    def test_stream_limit_keeps_unary_workers_available(self):
        self.manager.max_subscribers = 2
        streams = [self.stub.StreamSeatStatus(
            pb.StreamSeatStatusRequest(schedule_id="SCH001"), timeout=10) for _ in range(3)]
        for stream in streams:
            self.addCleanup(stream.cancel)
        next(streams[0])
        next(streams[1])
        with self.assertRaises(grpc.RpcError) as caught:
            next(streams[2])
        self.assertEqual(caught.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
        self.assertTrue(self.stub.BookSeat(self.request(), timeout=5).success)

    def test_snapshot_and_booking_race_never_loses_change(self):
        # Setiap percobaan menggunakan kursi berbeda. Tidak ada mock transport/DB.
        for index in range(10):
            seat_id = f"SCH001-{'AB'[index // 5]}{index % 5 + 1}"
            barrier = threading.Barrier(2)

            def subscribe():
                barrier.wait(timeout=5)
                return self.manager.subscribe("SCH001")

            def book():
                barrier.wait(timeout=5)
                return self.stub.BookSeat(self.request(seat_id=seat_id), timeout=5)

            with ThreadPoolExecutor(max_workers=2) as executor:
                subscription = executor.submit(subscribe)
                booking = executor.submit(book)
                subscriber, snapshot = subscription.result(timeout=10)
                self.assertTrue(booking.result(timeout=10).success)
            try:
                seat = next(s for s in snapshot["seats"] if s["seat_id"] == seat_id)
                if seat["status"] == "AVAILABLE":
                    update = subscriber.updates.get(timeout=2)
                    self.assertEqual(update["seats"][0]["seat_id"], seat_id)
                    self.assertEqual(update["seats"][0]["status"], "BOOKED")
                else:
                    self.assertEqual(seat["status"], "BOOKED")
            finally:
                self.manager.unsubscribe(subscriber)


class PubSubTests(unittest.TestCase):
    def test_bounded_queue_and_schedule_isolation(self):
        with tempfile.TemporaryDirectory(prefix="cinema-pubsub-") as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            manager = SeatManager(database, queue_size=1)
            slow, _ = manager.subscribe("SCH001")
            fast, _ = manager.subscribe("SCH001")
            other_schedule, _ = manager.subscribe("SCH002")
            manager.book(str(uuid.uuid4()), "SCH001", "SCH001-A1", "Pertama")
            self.assertEqual(fast.updates.get_nowait()["seats"][0]["seat_id"], "SCH001-A1")
            # Slow tidak mengonsumsi antrean. Booking kedua tetap berhasil.
            result = manager.book(str(uuid.uuid4()), "SCH001", "SCH001-A2", "Kedua")
            self.assertEqual(result["booking_status"], "CONFIRMED")
            self.assertTrue(slow.closed.is_set())
            self.assertTrue(slow.overflow)
            self.assertNotIn(slow, manager.subscribers)
            self.assertEqual(slow.updates.qsize(), 1)
            self.assertEqual(fast.updates.get_nowait()["seats"][0]["seat_id"], "SCH001-A2")
            with self.assertRaises(queue.Empty):
                other_schedule.updates.get_nowait()
            # Retry tidak menerbitkan event kedua.
            request_id = str(uuid.uuid4())
            manager.book(request_id, "SCH001", "SCH001-A3", "Ketiga")
            fast.updates.get_nowait()
            manager.book(request_id, "SCH001", "SCH001-A3", "Ketiga")
            self.assertTrue(fast.updates.empty())
            manager.close()
