"""Web Gateway & Server-Sent Events (SSE) untuk sistem reservasi bioskop gRPC.
Menjembatani antarmuka Web Browser (HTML/JS) ke backend gRPC CinemaService.

Fitur:
- Menyajikan Web UI interaktif (Single-Page App tanpa dependensi pihak ketiga).
- Real-time live seat update menggunakan Server-Sent Events (SSE) yang terhubung ke StreamSeatStatus gRPC.
- REST endpoint untuk pemesanan kursi (dengan dukungan timeout dan retry idempoten).
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
import queue
import select
import socket

# Pastikan root proyek masuk ke sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import grpc
from client.cinema_client import CinemaClient, BookingError
from generated import cinema_pb2 as pb

LOGGER = logging.getLogger("CinemaWeb")
GRPC_SERVER = os.environ.get("GRPC_SERVER", "localhost:50051")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP Server multithreaded agar stream SSE tidak memblokir request lain."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.stream_shutdown = threading.Event()
        super().__init__(*args, **kwargs)

    def server_close(self):
        self.stream_shutdown.set()
        super().server_close()


class CinemaWebHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Format log singkat
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_index()
        elif path.startswith("/static/"):
            self._serve_static(path)
        elif path == "/api/schedules":
            self._handle_get_schedules()
        elif path == "/api/seats":
            schedule_id = query.get("schedule_id", ["SCH001"])[0]
            self._handle_get_seats(schedule_id)
        elif path == "/api/stream":
            schedule_id = query.get("schedule_id", ["SCH001"])[0]
            self._handle_sse_stream(schedule_id)
        else:
            self.send_error(404, "Halaman tidak ditemukan")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/book":
            self._handle_post_book()
        elif parsed.path == "/api/benchmark":
            self._handle_post_benchmark()
        elif parsed.path == "/api/concurrency_test":
            self._handle_post_concurrency_test()
        else:
            self.send_error(404, "Endpoint tidak ditemukan")

    def _serve_index(self):
        html_file = os.path.join(os.path.dirname(__file__), "index.html")
        if os.path.exists(html_file):
            with open(html_file, "rb") as f:
                content = f.read()
        else:
            content = b"<h1>Cinema Web UI file not found</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_static(self, path):
        static_root = (Path(__file__).parent / "static").resolve()
        try:
            relative = urllib.parse.unquote(path[len("/static/"):])
            full_path = (static_root / relative).resolve()
            if not full_path.is_relative_to(static_root):
                self.send_error(403, "Akses file ditolak")
                return
            if not full_path.is_file():
                self.send_error(404, "File not found")
                return
        except (ValueError, OSError):
            self.send_error(404, "File not found")
            return
        
        content_type = "application/octet-stream"
        suffix = full_path.suffix.lower()
        if suffix == ".css":
            content_type = "text/css"
        elif suffix == ".js":
            content_type = "application/javascript"
        elif suffix in (".jpg", ".jpeg"):
            content_type = "image/jpeg"
        elif suffix == ".png":
            content_type = "image/png"
        elif suffix == ".webp":
            content_type = "image/webp"
        elif suffix == ".svg":
            content_type = "image/svg+xml"

        with open(full_path, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(content)

    def _handle_get_schedules(self):
        try:
            with CinemaClient(GRPC_SERVER) as client:
                schedules = client.get_schedules(timeout=3.0)
                data = [
                    {
                        "schedule_id": s.schedule_id,
                        "movie_title": s.movie_title,
                        "studio": s.studio,
                        "show_time": s.show_time,
                    }
                    for s in schedules
                ]
                self._send_json({"success": True, "schedules": data})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, status=500)

    def _handle_get_seats(self, schedule_id):
        try:
            with CinemaClient(GRPC_SERVER) as client:
                seats = client.get_seats(schedule_id, timeout=3.0)
                data = [
                    {
                        "seat_id": s.seat_id,
                        "seat_number": s.seat_number,
                        "schedule_id": s.schedule_id,
                        "status": "AVAILABLE" if s.status == pb.AVAILABLE else "BOOKED",
                    }
                    for s in seats
                ]
                self._send_json({"success": True, "schedule_id": schedule_id, "seats": data})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, status=500)

    def _handle_sse_stream(self, schedule_id):
        """Server-Sent Events (SSE) streaming ke browser."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        self.close_connection = True
        events = queue.Queue(maxsize=64)
        stopped = threading.Event()
        finished = threading.Event()
        errors = []
        client = CinemaClient(GRPC_SERVER)

        def receive():
            try:
                for event in client.stream_seat_status(schedule_id):
                    if stopped.is_set():
                        break
                    events.put_nowait(event)
            except Exception as error:
                errors.append(str(error))
            finally:
                finished.set()

        worker = threading.Thread(target=receive, daemon=True)
        worker.start()
        try:
            while not self.server.stream_shutdown.is_set():
                # Peer disconnect terdeteksi walau tidak ada booking baru.
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    break
                try:
                    event = events.get(timeout=0.5)
                except queue.Empty:
                    if finished.is_set():
                        if errors:
                            payload = json.dumps({"error": errors[0]})
                            self.wfile.write(f"event: stream_error\ndata: {payload}\n\n".encode())
                            self.wfile.flush()
                        break
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                else:
                    event_data = {
                        "schedule_id": event.schedule_id,
                        "event_type": "SNAPSHOT" if event.event_type == pb.SNAPSHOT else "UPDATE",
                        "timestamp": event.timestamp,
                        "seats": [
                            {
                                "seat_id": s.seat_id,
                                "seat_number": s.seat_number,
                                "status": "AVAILABLE" if s.status == pb.AVAILABLE else "BOOKED",
                            }
                            for s in event.seats
                        ],
                    }
                    payload = f"data: {json.dumps(event_data)}\n\n".encode("utf-8")
                    self.wfile.write(payload)
                    self.wfile.flush()
        except (OSError, ValueError):
            LOGGER.info("Browser menutup koneksi SSE")
        finally:
            stopped.set()
            client.close()
            worker.join(timeout=2)

    def _handle_post_book(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self._send_json({"success": False, "error": "Invalid JSON"}, status=400)
            return

        try:
            if not isinstance(body, dict):
                raise ValueError("Body harus objek JSON")
            schedule_id = body.get("schedule_id", "SCH001")
            seat_id = body.get("seat_id")
            customer_name = body.get("customer_name", "Web User")
            request_id = body.get("request_id")
            if not all(isinstance(value, str) and value.strip() for value in
                       (schedule_id, seat_id, customer_name, request_id)):
                raise ValueError("Jadwal, kursi, nama, dan request_id wajib diisi")
            uuid.UUID(request_id)
            timeout = float(body.get("timeout", 3.0))
            max_retries = int(body.get("max_retries", 3))
            if not 0 < timeout <= 30 or not 1 <= max_retries <= 5:
                raise ValueError("Timeout harus > 0 sampai 30; percobaan harus 1 sampai 5")
        except (ValueError, TypeError, OverflowError) as error:
            self._send_json({"success": False, "error": str(error), "final": True}, status=400)
            return

        retry_logs = []
        def on_retry(att, max_r, err, delay, rid):
            retry_logs.append({
                "attempt": att,
                "max_retries": max_r,
                "error": getattr(err, "code", lambda: "ERROR")().name,
                "delay": round(delay, 2),
                "request_id": rid,
            })

        start = time.perf_counter()
        try:
            with CinemaClient(GRPC_SERVER) as client:
                res = client.book_seat(
                    schedule_id=schedule_id,
                    seat_id=seat_id,
                    customer_name=customer_name,
                    timeout=timeout,
                    max_retries=max_retries,
                    request_id=request_id,
                    on_retry_callback=on_retry,
                )
                duration_ms = (time.perf_counter() - start) * 1000
                self._send_json({
                    "success": res.success,
                    "booking_id": res.booking_id,
                    "request_id": res.request_id,
                    "seat_id": res.seat_id,
                    "message": res.message,
                    "booking_status": pb.BookingStatus.Name(res.booking_status),
                    "duration_ms": round(duration_ms, 2),
                    "retry_logs": retry_logs,
                })
        except BookingError as err:
            uncertain = err.status_code in (grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNAVAILABLE)
            self._send_json({
                "success": False,
                "error": str(err),
                "request_id": err.request_id,
                "retry_logs": retry_logs,
                "status_unknown": uncertain,
                "final": not uncertain,
            }, status=503 if uncertain else 400)
        except grpc.RpcError as rpc_err:
            self._send_json({
                "success": False,
                "error": f"{rpc_err.code().name}: {rpc_err.details()}",
                "retry_logs": retry_logs,
            }, status=400)

    def _handle_post_concurrency_test(self):
        """Simulasi 5 klien secara bersamaan merebut 1 kursi yang sama."""
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            body = {}

        schedule_id = body.get("schedule_id", "SCH001")
        seat_id = body.get("seat_id", f"{schedule_id}-A1")
        
        from concurrent.futures import ThreadPoolExecutor

        results = []
        barrier = threading.Barrier(5)

        def attempt_book(i):
            client = CinemaClient(GRPC_SERVER)
            req_id = str(uuid.uuid4())
            cust_name = f"Client-{i+1}"
            try:
                # Sinkronkan agar kelima thread menembak bersamaan
                barrier.wait(timeout=2.0)
            except Exception:
                pass

            t0 = time.perf_counter()
            try:
                res = client.book_seat(
                    schedule_id=schedule_id,
                    seat_id=seat_id,
                    customer_name=cust_name,
                    request_id=req_id,
                    timeout=3.0,
                    max_retries=1,
                )
                dur = round((time.perf_counter() - t0) * 1000, 2)
                return {
                    "client": cust_name,
                    "request_id": req_id,
                    "status": pb.BookingStatus.Name(res.booking_status),
                    "success": res.success,
                    "message": res.message,
                    "booking_id": res.booking_id,
                    "latency_ms": dur,
                }
            except Exception as e:
                dur = round((time.perf_counter() - t0) * 1000, 2)
                return {
                    "client": cust_name,
                    "request_id": req_id,
                    "status": "ERROR",
                    "success": False,
                    "message": str(e),
                    "booking_id": "",
                    "latency_ms": dur,
                }
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futs = [executor.submit(attempt_book, i) for i in range(5)]
            results = [f.result() for f in futs]

        confirmed_count = sum(1 for r in results if r["status"] == "CONFIRMED")
        rejected_count = sum(1 for r in results if r["status"] == "REJECTED")

        self._send_json({
            "success": confirmed_count == 1 and rejected_count == 4,
            "seat_id": seat_id,
            "confirmed_count": confirmed_count,
            "rejected_count": rejected_count,
            "results": results,
        })

    def _handle_post_benchmark(self):
        """Menjalankan benchmark gRPC latensi & throughput."""
        from concurrent.futures import ThreadPoolExecutor
        concurrency_levels = [1, 5, 20]
        results = []
        total_per_test = 50

        for concurrency in concurrency_levels:
            def worker(_):
                c = CinemaClient(GRPC_SERVER)
                t0 = time.perf_counter()
                c.get_seats("SCH001", timeout=5.0)
                dur = (time.perf_counter() - t0) * 1000
                c.close()
                return dur

            start_t = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                lats = list(executor.map(worker, range(total_per_test)))
            tot_time = time.perf_counter() - start_t
            throughput = total_per_test / tot_time
            avg_lat = sum(lats) / len(lats)
            results.append({
                "concurrency": concurrency,
                "total_time": round(tot_time, 3),
                "throughput": round(throughput, 1),
                "avg_latency": round(avg_lat, 2),
            })
        self._send_json({"success": True, "results": results})


def run_web_server(port=8080):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    server_address = ("0.0.0.0", port)
    httpd = ThreadedHTTPServer(server_address, CinemaWebHandler)
    LOGGER.info("Web Server aktif di http://localhost:%d", port)
    LOGGER.info("Menghubungkan ke backend gRPC di %s", GRPC_SERVER)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Web Server dihentikan")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run_web_server(p)
