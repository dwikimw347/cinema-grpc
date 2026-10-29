"""Koordinasi booking dan pub/sub dalam SATU instance server."""

from dataclasses import dataclass, field
import queue
import threading

from .database import timestamp


class StreamLimitError(Exception):
    pass


@dataclass(eq=False)
class Subscriber:
    schedule_id: str
    updates: queue.Queue
    closed: threading.Event = field(default_factory=threading.Event)
    overflow: bool = False


class SeatManager:
    def __init__(self, database, queue_size=64, max_subscribers=16):
        self.database = database
        self.lock = threading.RLock()
        self.queue_size = queue_size
        self.max_subscribers = max_subscribers
        self.subscribers = set()

    def get_seats(self, schedule_id):
        with self.lock:
            return self.database.get_seats(schedule_id)

    def book(self, request_id, schedule_id, seat_id, customer_name):
        # Satu critical section: cek ID/status, commit, lalu enqueue update.
        # Tidak ada yield, penantian client, atau queue.put blocking di sini.
        with self.lock:
            result, updated_seat = self.database.book(
                request_id, schedule_id, seat_id, customer_name)
            if updated_seat is not None:
                event = {
                    "schedule_id": schedule_id,
                    "timestamp": timestamp(),
                    "seats": [updated_seat],
                }
                for subscriber in list(self.subscribers):
                    if subscriber.schedule_id != schedule_id:
                        continue
                    try:
                        subscriber.updates.put_nowait(event)
                    except queue.Full:
                        # Putuskan subscriber lambat; ia harus subscribe ulang
                        # untuk snapshot baru, bukan diam-diam kehilangan update.
                        subscriber.overflow = True
                        subscriber.closed.set()
                        self.subscribers.discard(subscriber)
            return result

    def subscribe(self, schedule_id):
        # Snapshot dan registrasi memakai lock yang sama dengan commit/publish.
        # Booking selalu masuk snapshot ATAU queue; tidak ada celah perubahan hilang.
        with self.lock:
            seats = self.database.get_seats(schedule_id)
            if len(self.subscribers) >= self.max_subscribers:
                raise StreamLimitError("Batas subscriber tercapai; coba lagi nanti")
            subscriber = Subscriber(schedule_id, queue.Queue(maxsize=self.queue_size))
            self.subscribers.add(subscriber)
            snapshot = {"schedule_id": schedule_id, "seats": seats, "timestamp": timestamp()}
            return subscriber, snapshot

    def unsubscribe(self, subscriber):
        with self.lock:
            self.subscribers.discard(subscriber)
            subscriber.closed.set()

    def close(self):
        with self.lock:
            for subscriber in self.subscribers:
                subscriber.closed.set()
            self.subscribers.clear()
