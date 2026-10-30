"""Dua client lokal independen. Demo dua komputer tetap dilakukan lewat LAN."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.demo_fault_tolerance import run_demo


if __name__ == "__main__":
    try:
        address = sys.argv[1] if len(sys.argv) > 1 else "localhost:50051"
        sys.exit(run_demo(address, racers=2))
    except Exception as error:
        print(f"DEMO GAGAL: {error}", file=sys.stderr)
        sys.exit(1)
