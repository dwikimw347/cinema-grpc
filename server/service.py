"""Validasi kontrak RPC dan pemetaan error gRPC."""

import logging
import queue
import sqlite3
import uuid
from contextlib import contextmanager

import grpc

from generated import cinema_pb2 as pb
from generated import cinema_pb2_grpc as pb_grpc
from .database import IdempotencyConflict, NotFoundError
from .seat_manager import StreamLimitError

LOGGER = logging.getLogger(__name__)


@contextmanager
def database_errors(context):
    try:
        yield
    except NotFoundError as error:
        context.abort(grpc.StatusCode.NOT_FOUND, str(error))
    except IdempotencyConflict as error:
        context.abort(grpc.StatusCode.ALREADY_EXISTS, str(error))
    except StreamLimitError as error:
        context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(error))
    except sqlite3.OperationalError:
        LOGGER.exception("Database tidak tersedia")
        context.abort(grpc.StatusCode.UNAVAILABLE, "Database sementara tidak tersedia")
    except sqlite3.DatabaseError:
        LOGGER.exception("Kesalahan integritas/database")
        context.abort(grpc.StatusCode.INTERNAL, "Operasi database gagal")


def require_text(value, name, context, limit=200):
    if not value.strip() or len(value) > limit:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                      f"{name} wajib diisi dan maksimal {limit} karakter")


def seat_message(seat):
    return pb.Seat(seat_id=seat["seat_id"], seat_number=seat["seat_number"],
                   schedule_id=seat["schedule_id"], status=pb.SeatStatus.Value(seat["status"]))


def event_message(event, event_type):
    return pb.SeatStatusEvent(
        schedule_id=event["schedule_id"], timestamp=event["timestamp"], event_type=event_type,
        seats=[seat_message(seat) for seat in event["seats"]])


class CinemaService(pb_grpc.CinemaServiceServicer):
    def __init__(self, manager):
        self.manager = manager

    def GetSchedules(self, request, context):
        with database_errors(context):
            rows = self.manager.database.get_schedules()
        return pb.GetSchedulesResponse(schedules=[pb.Schedule(**row) for row in rows])

    def GetSeats(self, request, context):
        require_text(request.schedule_id, "schedule_id", context)
        with database_errors(context):
            seats = self.manager.get_seats(request.schedule_id)
        return pb.GetSeatsResponse(seats=[seat_message(seat) for seat in seats])

    def BookSeat(self, request, context):
        require_text(request.schedule_id, "schedule_id", context)
        require_text(request.seat_id, "seat_id", context)
        require_text(request.customer_name, "customer_name", context)
        try:
            # Bentuk UUID berbeda (huruf besar/kecil) dinormalisasi ke ID sama.
            request_id = str(uuid.UUID(request.request_id))
        except (ValueError, AttributeError):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "request_id harus UUID valid")
        with database_errors(context):
            result = self.manager.book(request_id, request.schedule_id,
                                       request.seat_id, request.customer_name)
        return pb.BookSeatResponse(
            booking_id=result["booking_id"], request_id=result["request_id"],
            success=result["booking_status"] == "CONFIRMED", message=result["message"],
            seat_id=result["seat_id"], booking_status=pb.BookingStatus.Value(result["booking_status"]))

    def StreamSeatStatus(self, request, context):
        require_text(request.schedule_id, "schedule_id", context)
        with database_errors(context):
            subscriber, snapshot = self.manager.subscribe(request.schedule_id)
        try:
            callback = lambda: self.manager.unsubscribe(subscriber)
            if not context.add_callback(callback):
                return
            # Seluruh pengiriman jaringan dilakukan SETELAH lock dilepas.
            yield event_message(snapshot, pb.SNAPSHOT)
            while context.is_active() and not subscriber.closed.is_set():
                try:
                    event = subscriber.updates.get(timeout=0.25)
                except queue.Empty:
                    continue
                if not subscriber.closed.is_set():
                    yield event_message(event, pb.UPDATE)
            if subscriber.overflow and context.is_active():
                context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED,
                              "Subscriber terlalu lambat; subscribe ulang untuk snapshot terbaru")
        finally:
            self.manager.unsubscribe(subscriber)
