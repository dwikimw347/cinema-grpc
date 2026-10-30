"""Pengujian unit dan integrasi untuk CinemaClient."""

import queue
import threading
import time
import unittest
import uuid
from unittest.mock import MagicMock

import grpc

from client.cinema_client import CinemaClient, BookingError
from generated import cinema_pb2 as pb
from tests.support import RpcTestCase


class ClientIntegrationTests(RpcTestCase):
    def setUp(self):
        super().setUp()
        self.client = CinemaClient(f"127.0.0.1:{self.port}")
        self.addCleanup(self.client.close)

    def test_get_schedules(self):
        schedules = self.client.get_schedules()
        self.assertEqual(len(schedules), 3)
        self.assertTrue(any(s.schedule_id == "SCH001" for s in schedules))

    def test_get_seats(self):
        seats = self.client.get_seats("SCH001")
        self.assertEqual(len(seats), 20)
        self.assertTrue(all(s.status == pb.AVAILABLE for s in seats))

    def test_book_seat_success(self):
        response = self.client.book_seat(
            schedule_id="SCH001",
            seat_id="SCH001-A1",
            customer_name="Budi Santoso",
        )
        self.assertTrue(response.success)
        self.assertEqual(response.booking_status, pb.CONFIRMED)
        self.assertEqual(response.seat_id, "SCH001-A1")

        # Verifikasi kursi telah berubah menjadi BOOKED
        seats = self.client.get_seats("SCH001")
        a1 = next(s for s in seats if s.seat_id == "SCH001-A1")
        self.assertEqual(a1.status, pb.BOOKED)

    def test_idempotent_retry_identical_request_id(self):
        fixed_rid = str(uuid.uuid4())
        resp1 = self.client.book_seat(
            schedule_id="SCH001",
            seat_id="SCH001-B2",
            customer_name="Dewi Lestari",
            request_id=fixed_rid,
        )
        self.assertTrue(resp1.success)

        # Pengulangan (retry) dengan request_id yang sama
        resp2 = self.client.book_seat(
            schedule_id="SCH001",
            seat_id="SCH001-B2",
            customer_name="Dewi Lestari",
            request_id=fixed_rid,
        )
        self.assertEqual(resp1.booking_id, resp2.booking_id)
        self.assertEqual(resp1.request_id, resp2.request_id)
        self.assertEqual(resp1.message, resp2.message)
        self.assertEqual(self.booking_count(confirmed_only=True), 1)

    def test_streaming_updates(self):
        events = queue.Queue()
        stop_thread = threading.Event()

        def stream_worker():
            for event in self.client.stream_seat_status("SCH001"):
                events.put(event)
                if stop_thread.is_set():
                    break

        thread = threading.Thread(target=stream_worker, daemon=True)
        thread.start()

        # Event pertama harus SNAPSHOT
        first_event = events.get(timeout=3.0)
        self.assertEqual(first_event.event_type, pb.SNAPSHOT)
        self.assertEqual(len(first_event.seats), 20)

        # Lakukan pemesanan kursi
        self.client.book_seat("SCH001", "SCH001-C3", "Stream Tester")

        # Event kedua harus UPDATE untuk C3
        second_event = events.get(timeout=3.0)
        self.assertEqual(second_event.event_type, pb.UPDATE)
        self.assertEqual(len(second_event.seats), 1)
        self.assertEqual(second_event.seats[0].seat_id, "SCH001-C3")
        self.assertEqual(second_event.seats[0].status, pb.BOOKED)

        stop_thread.set()

    def test_retry_callback_invoked_on_simulated_transient_failure(self):
        """Uji bahwa saat terjadi DEADLINE_EXCEEDED, retry dipanggil dengan ID yang sama."""
        retry_records = []

        def on_retry(attempt, max_retries, error, delay, request_id):
            retry_records.append((attempt, request_id))

        # Mock stub untuk melempar DEADLINE_EXCEEDED pada percobaan ke-1, lalu sukses pada ke-2
        real_stub = self.client._stub
        mock_stub = MagicMock()
        mock_error = grpc.RpcError()
        mock_error.code = lambda: grpc.StatusCode.DEADLINE_EXCEEDED
        mock_error.details = lambda: "Simulated deadline timeout"

        real_response = pb.BookSeatResponse(
            booking_id="bk-mock-1",
            request_id="will-be-overwritten",
            success=True,
            message="Sukses setelah retry",
            seat_id="SCH001-D4",
            booking_status=pb.CONFIRMED,
        )

        call_count = [0]
        fixed_rid = str(uuid.uuid4())

        def side_effect(request, timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                raise mock_error
            real_response.request_id = request.request_id
            return real_response

        mock_stub.BookSeat.side_effect = side_effect
        self.client._stub = mock_stub

        try:
            result = self.client.book_seat(
                schedule_id="SCH001",
                seat_id="SCH001-D4",
                customer_name="Uji Retry",
                request_id=fixed_rid,
                max_retries=3,
                base_delay=0.01,
                on_retry_callback=on_retry,
            )
            self.assertTrue(result.success)
            self.assertEqual(call_count[0], 2)
            self.assertEqual(len(retry_records), 1)
            # Pastikan attempt ke-1 gagal dan request_id tetap sama persis!
            self.assertEqual(retry_records[0][0], 1)
            self.assertEqual(retry_records[0][1], fixed_rid)
        finally:
            self.client._stub = real_stub


if __name__ == "__main__":
    unittest.main()
