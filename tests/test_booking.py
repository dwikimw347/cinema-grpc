import sqlite3
import uuid

import grpc

from generated import cinema_pb2 as pb
from tests.support import RpcTestCase


class BookingTests(RpcTestCase):
    def test_get_schedules(self):
        response = self.stub.GetSchedules(pb.GetSchedulesRequest(), timeout=5)
        self.assertEqual(len(response.schedules), 3)
        self.assertTrue(all(s.movie_title and s.studio and s.show_time for s in response.schedules))

    def test_get_seats(self):
        for schedule_id in ("SCH001", "SCH002", "SCH003"):
            response = self.stub.GetSeats(pb.GetSeatsRequest(schedule_id=schedule_id), timeout=5)
            self.assertEqual(len(response.seats), 20)
            self.assertTrue(all(s.schedule_id == schedule_id and s.status == pb.AVAILABLE
                                for s in response.seats))
            self.assertEqual(len({s.seat_number for s in response.seats}), 20)

    def test_successful_booking(self):
        result = self.stub.BookSeat(self.request(), timeout=5)
        self.assertTrue(result.success)
        self.assertEqual(result.booking_status, pb.CONFIRMED)
        seats = self.stub.GetSeats(pb.GetSeatsRequest(schedule_id="SCH001"), timeout=5).seats
        self.assertEqual(next(s.status for s in seats if s.seat_id == result.seat_id), pb.BOOKED)
        self.assertEqual(self.booking_count(True), 1)

    def test_duplicate_returns_identical_result(self):
        request = self.request()
        first = self.stub.BookSeat(request, timeout=5)
        repeated = self.stub.BookSeat(request, timeout=5)
        self.assertEqual(first, repeated)
        self.assertEqual(self.booking_count(), 1)

    def test_idempotency_conflict(self):
        request = self.request()
        original = self.stub.BookSeat(request, timeout=5)
        for change in ({"seat_id": "SCH001-A2"}, {"customer_name": "Nama berbeda"},
                       {"schedule_id": "SCH002", "seat_id": "SCH002-A1"}):
            other = pb.BookSeatRequest()
            other.CopyFrom(request)
            for name, value in change.items():
                setattr(other, name, value)
            with self.assertRaises(grpc.RpcError) as caught:
                self.stub.BookSeat(other, timeout=5)
            self.assertEqual(caught.exception.code(), grpc.StatusCode.ALREADY_EXISTS)
        self.assertEqual(self.stub.BookSeat(request, timeout=5), original)
        self.assertEqual(self.booking_count(), 1)

    def test_success_and_rejection_survive_restart(self):
        success_request = self.request()
        failed_request = self.request(customer_name="Pelanggan kedua")
        success = self.stub.BookSeat(success_request, timeout=5)
        failure = self.stub.BookSeat(failed_request, timeout=5)
        self.assertFalse(failure.success)
        self.stop_server()
        self.start_server()  # Database sama, service/manager/server baru.
        self.assertEqual(self.stub.BookSeat(success_request, timeout=5), success)
        self.assertEqual(self.stub.BookSeat(failed_request, timeout=5), failure)
        self.assertEqual(self.booking_count(), 2)
        self.assertEqual(self.booking_count(True), 1)

    def test_validation_and_missing_resources(self):
        for changes in ({"request_id": "bukan-uuid"}, {"customer_name": " "},
                        {"schedule_id": ""}, {"seat_id": ""}):
            with self.assertRaises(grpc.RpcError) as caught:
                self.stub.BookSeat(self.request(**changes), timeout=5)
            self.assertEqual(caught.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        with self.assertRaises(grpc.RpcError) as caught:
            self.stub.BookSeat(self.request(seat_id="SCH002-A1"), timeout=5)
        self.assertEqual(caught.exception.code(), grpc.StatusCode.NOT_FOUND)
        with self.assertRaises(grpc.RpcError) as caught:
            self.stub.GetSeats(pb.GetSeatsRequest(schedule_id="missing"), timeout=5)
        self.assertEqual(caught.exception.code(), grpc.StatusCode.NOT_FOUND)
        self.assertEqual(self.booking_count(), 0)

    def test_database_constraint_blocks_second_confirmed_booking(self):
        self.stub.BookSeat(self.request(), timeout=5)
        with self.manager.database.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                with connection:
                    connection.execute(
                        """INSERT INTO bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (str(uuid.uuid4()), str(uuid.uuid4()), "SCH001", "SCH001-A1",
                         "Duplikat SQL", "CONFIRMED", "Tidak boleh", "2026-11-05T00:00:00Z"))
        self.assertEqual(self.booking_count(True), 1)

    def test_transaction_rolls_back_when_insert_fails(self):
        # Trigger SQLite sungguhan mensimulasikan kegagalan setelah UPDATE seats.
        with self.manager.database.connect() as connection:
            connection.execute("""CREATE TRIGGER fail_booking BEFORE INSERT ON bookings
                                  BEGIN SELECT RAISE(ABORT, 'uji rollback'); END""")
        with self.assertLogs("server.service", level="ERROR"):
            with self.assertRaises(grpc.RpcError) as caught:
                self.stub.BookSeat(self.request(), timeout=5)
        self.assertEqual(caught.exception.code(), grpc.StatusCode.INTERNAL)
        self.assertEqual(self.booking_count(), 0)
        seats = self.stub.GetSeats(pb.GetSeatsRequest(schedule_id="SCH001"), timeout=5).seats
        self.assertTrue(all(s.status == pb.AVAILABLE for s in seats))

    def test_stream_snapshot_update_and_disconnect(self):
        streams = [self.stub.StreamSeatStatus(
            pb.StreamSeatStatusRequest(schedule_id="SCH001"), timeout=10) for _ in range(2)]
        for stream in streams:
            self.addCleanup(stream.cancel)
            snapshot = next(stream)
            self.assertEqual(snapshot.event_type, pb.SNAPSHOT)
            self.assertEqual(len(snapshot.seats), 20)
            self.assertTrue(snapshot.timestamp)
        self.stub.BookSeat(self.request(), timeout=5)
        for stream in streams:
            update = next(stream)
            self.assertEqual(update.event_type, pb.UPDATE)
            self.assertEqual(update.schedule_id, "SCH001")
            self.assertEqual(len(update.seats), 1)
            self.assertEqual(update.seats[0].seat_id, "SCH001-A1")
            self.assertEqual(update.seats[0].status, pb.BOOKED)
            self.assertTrue(update.timestamp)
            stream.cancel()
        self.wait_subscribers(0)

    def test_stream_unknown_schedule(self):
        stream = self.stub.StreamSeatStatus(pb.StreamSeatStatusRequest(schedule_id="missing"), timeout=5)
        with self.assertRaises(grpc.RpcError) as caught:
            next(stream)
        self.assertEqual(caught.exception.code(), grpc.StatusCode.NOT_FOUND)
        self.wait_subscribers(0)
