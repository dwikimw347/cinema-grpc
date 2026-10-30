from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from client.cinema_client import CinemaClient
from scripts.demo_fault_tolerance import demonstrate_deduplication, run_demo
from scripts.demo_support import local_demo_server
from tests.support import RpcTestCase


class DemoTests(RpcTestCase):
    def test_demo_can_repeat_on_same_database(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_demo(f"127.0.0.1:{self.port}"), 0)
            self.assertEqual(run_demo(f"127.0.0.1:{self.port}", racers=2), 0)
        self.assertEqual(self.booking_count(True), 6)
        self.wait_subscribers(0)

    def test_demo_refuses_success_when_no_seats_available(self):
        with self.manager.database.connect() as connection:
            with connection:
                connection.execute("UPDATE seats SET status = ?", ("BOOKED",))
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "tiga kursi"):
                run_demo(f"127.0.0.1:{self.port}")
        self.assertNotIn("SELURUH SKENARIO", output.getvalue())


class FaultInjectionTests(unittest.TestCase):
    def test_real_timeout_after_commit_has_one_booking(self):
        with local_demo_server() as (address, manager):
            with CinemaClient(address) as client, redirect_stdout(io.StringIO()) as output:
                self.assertTrue(client.ping())
                response = demonstrate_deduplication(client, "SCH001", "SCH001-A1", require_timeout=True)
            self.assertTrue(response.success)
            self.assertIn("DEADLINE_EXCEEDED", output.getvalue())
            with manager.database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 1)

    def test_demo_process_exit_code_reflects_failure(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        success = subprocess.run([sys.executable, "scripts/demo_fault_tolerance.py", "--local"],
                                 cwd=root, capture_output=True, text=True, encoding="utf-8", env=env, timeout=20)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertIn("SELURUH SKENARIO", success.stdout)
        failure = subprocess.run([sys.executable, "scripts/demo_fault_tolerance.py", "127.0.0.1:1"],
                                 cwd=root, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)
        self.assertEqual(failure.returncode, 1)
        self.assertIn("DEMO GAGAL", failure.stderr)
        self.assertNotIn("SELURUH SKENARIO", failure.stdout)
