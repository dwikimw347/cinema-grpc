"""Pemeriksaan read-only; bukan aplikasi client reservasi."""

import argparse
from pathlib import Path
import sys

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generated import cinema_pb2 as pb
from generated import cinema_pb2_grpc as pb_grpc


def main():
    parser = argparse.ArgumentParser(description="Periksa koneksi dan RPC daftar jadwal")
    parser.add_argument("--address", default="localhost:50051")
    args = parser.parse_args()
    try:
        with grpc.insecure_channel(args.address) as channel:
            grpc.channel_ready_future(channel).result(timeout=5)
            response = pb_grpc.CinemaServiceStub(channel).GetSchedules(
                pb.GetSchedulesRequest(), timeout=5)
            print(f"Server terhubung. Jumlah jadwal: {len(response.schedules)}")
            for schedule in response.schedules:
                print(f"{schedule.schedule_id} | {schedule.movie_title} | "
                      f"{schedule.studio} | {schedule.show_time}")
    except (grpc.FutureTimeoutError, grpc.RpcError) as error:
        print(f"Server belum dapat dihubungi: {args.address} ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
