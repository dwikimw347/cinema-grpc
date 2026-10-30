import http.client
import json
import socket
import threading
import uuid

from client.cinema_client import CinemaClient
from scripts.demo_support import local_demo_server
from tests.support import RpcTestCase
import web.server as web


class WebTests(RpcTestCase):
    def setUp(self):
        super().setUp()
        previous = web.GRPC_SERVER
        web.GRPC_SERVER = f"127.0.0.1:{self.port}"
        self.addCleanup(setattr, web, "GRPC_SERVER", previous)
        self.httpd = web.ThreadedHTTPServer(("127.0.0.1", 0), web.CinemaWebHandler)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_web)

    def close_web(self):
        self.httpd.stream_shutdown.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.worker.join(timeout=2)

    def request_http(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            headers = {"Content-Type": "application/json"}
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_static_traversal_rejected_and_image_works(self):
        for path in ("/static/../../requirements.txt", "/static/%2e%2e/%2e%2e/requirements.txt",
                     "/static/..%5c..%5crequirements.txt"):
            status, body = self.request_http("GET", path)
            self.assertIn(status, (403, 404))
            self.assertNotIn(b"grpcio==", body)
        status, body = self.request_http("GET", "/static/images/poster_nusantara.jpg")
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 100)

    def test_sse_disconnect_cleans_subscriber_without_booking(self):
        for _ in range(3):
            connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
            connection.request("GET", "/api/stream?schedule_id=SCH001")
            response = connection.getresponse()
            self.assertIn(b"SNAPSHOT", response.fp.readline())
            response.fp.readline()
            response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            response.close()
            connection.close()
            self.wait_subscribers(0)

    def test_sse_shutdown_cleans_idle_stream(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        connection.request("GET", "/api/stream?schedule_id=SCH001")
        response = connection.getresponse()
        self.assertIn(b"SNAPSHOT", response.fp.readline())
        self.httpd.stream_shutdown.set()
        self.wait_subscribers(0)
        response.close()
        connection.close()

    def test_rejected_request_then_new_operation(self):
        payload = dict(request_id=str(uuid.uuid4()), schedule_id="SCH001",
                       seat_id="SCH001-A1", customer_name="Pertama")
        self.assertTrue(json.loads(self.request_http("POST", "/api/book", payload)[1])["success"])
        payload["request_id"] = str(uuid.uuid4())
        rejected = json.loads(self.request_http("POST", "/api/book", payload)[1])
        self.assertEqual(rejected["booking_status"], "REJECTED")
        payload.update(request_id=str(uuid.uuid4()), seat_id="SCH001-A2")
        self.assertTrue(json.loads(self.request_http("POST", "/api/book", payload)[1])["success"])

    def test_invalid_json_fields_do_not_crash_handler(self):
        for payload in ([], {"timeout": "invalid"}, {"request_id": "bukan-uuid"}):
            status, body = self.request_http("POST", "/api/book", payload)
            self.assertEqual(status, 400)
            self.assertTrue(json.loads(body)["final"])

    def test_race_on_booked_seat_does_not_report_success(self):
        self.stub.BookSeat(self.request(), timeout=5)
        status, body = self.request_http("POST", "/api/concurrency_test",
                                         {"schedule_id": "SCH001", "seat_id": "SCH001-A1"})
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(result["success"])
        self.assertEqual(result["confirmed_count"], 0)
        self.assertEqual(result["rejected_count"], 5)

    def test_unknown_result_keeps_id_for_retry_after_commit(self):
        with local_demo_server() as (address, manager):
            web.GRPC_SERVER = address
            with CinemaClient(address) as client:
                self.assertTrue(client.ping())
            payload = dict(request_id=str(uuid.uuid4()), schedule_id="SCH001",
                           seat_id="SCH001-A1", customer_name="Timeout HTTP",
                           timeout=0.15, max_retries=1)
            status, body = self.request_http("POST", "/api/book", payload)
            result = json.loads(body)
            self.assertEqual(status, 503)
            self.assertTrue(result["status_unknown"])
            self.assertFalse(result["final"])
            self.assertEqual(result["request_id"], payload["request_id"])
            status, body = self.request_http("POST", "/api/book", payload)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["success"])
            with manager.database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1)
