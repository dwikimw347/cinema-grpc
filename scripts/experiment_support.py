"""Shared helpers for isolated, real-gRPC experiments."""

from contextlib import contextmanager
from pathlib import Path
import tempfile

from server.main import create_server


@contextmanager
def local_server(database_path=None):
    """Run the production service on a random loopback port and isolated DB."""
    temporary = None
    if database_path is None:
        temporary = tempfile.TemporaryDirectory(prefix="cinema-experiment-")
        database_path = Path(temporary.name) / "cinema.db"
    server, manager, port = create_server(database_path, "127.0.0.1:0")
    server.start()
    try:
        yield f"127.0.0.1:{port}", manager
    finally:
        manager.close()
        server.stop(grace=1).wait()
        if temporary is not None:
            temporary.cleanup()


def booking_counts(manager):
    with manager.database.connect() as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS total,
                      SUM(booking_status = 'CONFIRMED') AS confirmed,
                      SUM(booking_status = 'REJECTED') AS rejected
               FROM bookings"""
        ).fetchone()
        seat = connection.execute(
            "SELECT status FROM seats WHERE schedule_id = ? AND seat_id = ?",
            ("SCH001", "SCH001-A1"),
        ).fetchone()
        duplicate_confirmed = connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT schedule_id, seat_id FROM bookings
                   WHERE booking_status = 'CONFIRMED'
                   GROUP BY schedule_id, seat_id HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0]
        return {
            "total": row["total"],
            "confirmed": row["confirmed"] or 0,
            "rejected": row["rejected"] or 0,
            "seat_status": seat["status"],
            "duplicate_confirmed_seats": duplicate_confirmed,
        }
