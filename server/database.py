"""SQLite: satu koneksi per operasi, transaksi atomik, dan hasil request persisten."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


class NotFoundError(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY,
                    movie_title TEXT NOT NULL,
                    studio TEXT NOT NULL,
                    show_time TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seats (
                    seat_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id),
                    seat_number TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'BOOKED')),
                    UNIQUE (schedule_id, seat_number),
                    UNIQUE (schedule_id, seat_id)
                );
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    schedule_id TEXT NOT NULL,
                    seat_id TEXT NOT NULL,
                    customer_name TEXT NOT NULL CHECK (length(trim(customer_name)) > 0),
                    booking_status TEXT NOT NULL
                        CHECK (booking_status IN ('CONFIRMED', 'REJECTED')),
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (schedule_id, seat_id)
                        REFERENCES seats(schedule_id, seat_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_seat
                    ON bookings(schedule_id, seat_id)
                    WHERE booking_status = 'CONFIRMED';
            """)
            # Seed berulang tidak menimpa kursi/status/booking yang sudah ada.
            with connection:
                connection.executemany(
                    "INSERT OR IGNORE INTO schedules VALUES (?, ?, ?, ?)",
                    [
                        ("SCH001", "Petualangan Nusantara", "Studio 1", "2026-11-05T13:00:00+07:00"),
                        ("SCH002", "Misteri Kota", "Studio 2", "2026-11-05T16:00:00+07:00"),
                        ("SCH003", "Langit Senja", "Studio 3", "2026-11-05T19:00:00+07:00"),
                    ],
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO seats VALUES (?, ?, ?, ?)",
                    [(f"SCH{n:03d}-{row}{col}", f"SCH{n:03d}", f"{row}{col}", "AVAILABLE")
                     for n in range(1, 4) for row in "ABCD" for col in range(1, 6)],
                )

    def get_schedules(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM schedules ORDER BY schedule_id")]

    def get_seats(self, schedule_id):
        with self.connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM schedules WHERE schedule_id = ?", (schedule_id,)
            ).fetchone():
                raise NotFoundError("Jadwal tidak ditemukan")
            return [dict(row) for row in connection.execute(
                "SELECT * FROM seats WHERE schedule_id = ? ORDER BY seat_number", (schedule_id,))]

    def book(self, request_id, schedule_id, seat_id, customer_name):
        """Kembalikan (hasil tersimpan, kursi_baru_atau_None).

        BEGIN IMMEDIATE mengunci writer sebelum read-check-write. Lock Python
        di SeatManager melindungi koordinasi transaksi dan publish satu proses.
        """
        with self.connect() as connection:
            with connection:  # Commit jika sukses, rollback jika exception.
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute(
                    "SELECT * FROM bookings WHERE request_id = ?", (request_id,)
                ).fetchone()
                if previous is not None:
                    if (previous["schedule_id"], previous["seat_id"], previous["customer_name"]) != (
                        schedule_id, seat_id, customer_name
                    ):
                        raise IdempotencyConflict("request_id sudah digunakan dengan payload berbeda")
                    return dict(previous), None
                seat = connection.execute(
                    "SELECT * FROM seats WHERE schedule_id = ? AND seat_id = ?",
                    (schedule_id, seat_id),
                ).fetchone()
                if seat is None:
                    raise NotFoundError("Jadwal atau kursi tidak ditemukan pada jadwal tersebut")
                success = seat["status"] == "AVAILABLE"
                result = {
                    "booking_id": str(uuid.uuid4()),
                    "request_id": request_id,
                    "schedule_id": schedule_id,
                    "seat_id": seat_id,
                    "customer_name": customer_name,
                    "booking_status": "CONFIRMED" if success else "REJECTED",
                    "message": "Pemesanan berhasil" if success else "Kursi sudah dipesan",
                    "created_at": timestamp(),
                }
                if success:
                    connection.execute(
                        "UPDATE seats SET status = ? WHERE schedule_id = ? AND seat_id = ?",
                        ("BOOKED", schedule_id, seat_id),
                    )
                connection.execute(
                    """INSERT INTO bookings
                       (booking_id, request_id, schedule_id, seat_id, customer_name,
                        booking_status, message, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    tuple(result.values()),
                )
            # Keluar dari transaksi terlebih dahulu sebelum hasil dipublikasikan.
            updated_seat = dict(seat) if success else None
            if updated_seat is not None:
                updated_seat["status"] = "BOOKED"
            return result, updated_seat
