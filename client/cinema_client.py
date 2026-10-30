"""Client gRPC untuk sistem reservasi tiket bioskop.

Mendukung:
1. Pemilihan jadwal (GetSchedules).
2. Pengecekan denah kursi (GetSeats).
3. Pemesanan kursi (BookSeat) dengan penanganan timeout, exponential backoff,
   dan penggunaan request_id yang identik pada setiap retry (idempoten / at-most-once).
4. Pembaruan status kursi real-time melalui server streaming (StreamSeatStatus).
"""

import logging
import random
import time
import uuid
from typing import Callable, Generator, List, Optional

import grpc

from generated import cinema_pb2 as pb
from generated import cinema_pb2_grpc as pb_grpc

LOGGER = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.UNAVAILABLE,
}


class BookingError(Exception):
    """Kesalahan umum pemesanan kursi."""
    def __init__(self, message: str, request_id: str, status_code: Optional[grpc.StatusCode] = None):
        super().__init__(message)
        self.request_id = request_id
        self.status_code = status_code


class CinemaClient:
    """Wrapper client gRPC berkemampuan retry idempoten dan live streaming."""

    def __init__(self, address: str = "localhost:50051", channel_options: Optional[list] = None):
        self.address = address
        self.options = channel_options or [
            ("grpc.keepalive_time_ms", 30000),
            ("grpc.keepalive_timeout_ms", 5000),
            ("grpc.keepalive_permit_without_calls", True),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        self._channel = grpc.insecure_channel(self.address, options=self.options)
        self._stub = pb_grpc.CinemaServiceStub(self._channel)

    def close(self):
        """Menutup koneksi channel gRPC."""
        if self._channel is not None:
            self._channel.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def ping(self, timeout: float = 3.0) -> bool:
        """Memeriksa apakah server dapat dijangkau."""
        try:
            grpc.channel_ready_future(self._channel).result(timeout=timeout)
            return True
        except (grpc.FutureTimeoutError, grpc.RpcError):
            return False

    def get_schedules(self, timeout: float = 5.0) -> List[pb.Schedule]:
        """Mengambil seluruh jadwal pemutaran film."""
        request = pb.GetSchedulesRequest()
        response = self._stub.GetSchedules(request, timeout=timeout)
        return list(response.schedules)

    def get_seats(self, schedule_id: str, timeout: float = 5.0) -> List[pb.Seat]:
        """Mengambil seluruh status kursi untuk jadwal tertentu."""
        request = pb.GetSeatsRequest(schedule_id=schedule_id)
        response = self._stub.GetSeats(request, timeout=timeout)
        return list(response.seats)

    def book_seat(
        self,
        schedule_id: str,
        seat_id: str,
        customer_name: str,
        timeout: float = 3.0,
        max_retries: int = 3,
        base_delay: float = 0.5,
        backoff_multiplier: float = 2.0,
        request_id: Optional[str] = None,
        on_retry_callback: Optional[Callable[[int, int, Exception, float, str], None]] = None,
    ) -> pb.BookSeatResponse:
        """Memesan kursi dengan mekanisme timeout dan retry idempoten.

        Parameter:
            schedule_id: ID jadwal (misal: 'SCH001').
            seat_id: ID kursi (misal: 'SCH001-B3').
            customer_name: Nama pemesan.
            timeout: Batas waktu tiap percobaan RPC dalam detik.
            max_retries: Jumlah maksimum percobaan (termasuk percobaan pertama).
            base_delay: Jeda dasar exponential backoff dalam detik.
            backoff_multiplier: Pengali jeda tiap percobaan.
            request_id: UUID idempoten. Jika None, dibuatkan UUID baru.
                        Saat retry terjadi, ID ini TETAP SAMA agar server
                        mengenalinya sebagai at-most-once deduplication.
            on_retry_callback: Callback opsional (attempt, max_retries, error, delay, request_id)
                               untuk visualisasi log di UI/CLI.
        """
        if max_retries < 1 or timeout <= 0 or base_delay < 0 or backoff_multiplier < 1:
            raise ValueError("Percobaan/timeout harus positif; delay >= 0 dan multiplier >= 1")
        if not request_id:
            request_id = str(uuid.uuid4())

        request = pb.BookSeatRequest(
            request_id=request_id,
            schedule_id=schedule_id,
            seat_id=seat_id,
            customer_name=customer_name,
        )

        last_error: Optional[grpc.RpcError] = None

        for attempt in range(1, max_retries + 1):
            try:
                LOGGER.debug(
                    "Mengirim BookSeat (percobaan %d/%d) ID=%s kursi=%s pemesan=%s timeout=%.2fs",
                    attempt, max_retries, request_id, seat_id, customer_name, timeout
                )
                response = self._stub.BookSeat(request, timeout=timeout)
                return response

            except grpc.RpcError as error:
                last_error = error
                code = error.code()

                # Cek apakah error dapat di-retry (DEADLINE_EXCEEDED atau UNAVAILABLE)
                is_retryable = code in RETRYABLE_STATUS_CODES
                if not is_retryable or attempt >= max_retries:
                    LOGGER.error(
                        "Pemesanan gagal pada percobaan %d/%d (code=%s, details=%s)",
                        attempt, max_retries, code, error.details()
                    )
                    raise BookingError(
                        f"Pemesanan gagal ({code.name}): {error.details()}",
                        request_id=request_id,
                        status_code=code,
                    ) from error

                # Hitung delay exponential backoff dengan sedikit jitter acak (0-15%)
                jitter = random.uniform(0.0, 0.15)
                delay = (base_delay * (backoff_multiplier ** (attempt - 1))) + jitter

                LOGGER.warning(
                    "[RETRY #%d/%d] Terjadi gangguan (%s). Mengulang dalam %.2fs dengan ID TETAP SAMA (%s)...",
                    attempt, max_retries, code.name, delay, request_id
                )

                if on_retry_callback:
                    try:
                        on_retry_callback(attempt, max_retries, error, delay, request_id)
                    except Exception:
                        pass

                time.sleep(delay)

        raise BookingError(
            f"Batas percobaan ({max_retries}) terlampaui. Error terakhir: {last_error}",
            request_id=request_id,
        )

    def stream_seat_status(
        self,
        schedule_id: str,
        timeout: Optional[float] = None,
    ) -> Generator[pb.SeatStatusEvent, None, None]:
        """Membuka server streaming untuk pembaruan status kursi secara langsung.

        Menghasilkan event:
        - SNAPSHOT: memuat seluruh kursi saat koneksi pertama kali terbuka.
        - UPDATE: memuat kursi yang baru saja berubah statusnya.
        """
        request = pb.StreamSeatStatusRequest(schedule_id=schedule_id)
        stream = self._stub.StreamSeatStatus(request, timeout=timeout)
        try:
            for event in stream:
                yield event
        except grpc.RpcError as error:
            if error.code() != grpc.StatusCode.CANCELLED:
                LOGGER.info("Stream terputus: %s (%s)", error.code(), error.details())
                raise
        finally:
            stream.cancel()
