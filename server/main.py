"""Entry point: python -m server.main."""

import argparse
from concurrent import futures
import logging
from pathlib import Path

import grpc

from generated import cinema_pb2_grpc as pb_grpc
from .database import Database
from .seat_manager import SeatManager
from .service import CinemaService

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "cinema.db"


def create_server(db_path=DEFAULT_DATABASE, address="localhost:50051", max_workers=32,
                  max_subscribers=16, queue_size=64):
    if max_workers <= max_subscribers or max_subscribers < 1 or queue_size < 1:
        raise ValueError("workers harus > max_subscribers >= 1 dan queue_size >= 1")
    database = Database(db_path)
    database.initialize()
    manager = SeatManager(database, queue_size, max_subscribers)
    options = [
        ("grpc.http2.min_ping_interval_without_data_ms", 1000),
        ("grpc.http2.max_ping_strikes", 0),
        ("grpc.keepalive_permit_without_calls", 1),
    ]
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers), options=options)
    pb_grpc.add_CinemaServiceServicer_to_server(CinemaService(manager), server)
    port = server.add_insecure_port(address)
    if port == 0:
        raise RuntimeError(f"Tidak dapat membuka alamat {address}")
    return server, manager, port


def main():
    parser = argparse.ArgumentParser(description="Server reservasi tiket bioskop gRPC")
    parser.add_argument("--host", default="localhost", help="Gunakan IP LAN/0.0.0.0 untuk dua komputer")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server, manager, _ = create_server(args.db, f"{args.host}:{args.port}")
    server.start()
    logging.info("Server gRPC berjalan di %s:%s | database: %s", args.host, args.port, args.db.resolve())
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logging.info("Menghentikan server...")
    finally:
        manager.close()
        server.stop(grace=3).wait()
        logging.info("Server berhenti dengan aman")


if __name__ == "__main__":
    main()
