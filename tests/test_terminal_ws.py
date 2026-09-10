from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from queue import Queue
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


from universe_app.terminal_ws import (  # noqa: E402
    TERMINAL_WS_CLOSE_SERVICE_RESTART,
    _SerializedWebSocketSender,
    decode_ws_frame,
    encode_ws_frame,
    pump_terminal_socket,
)


class _RecordingSocket:
    def __init__(self, expected: int) -> None:
        self.expected = expected
        self.frames: list[bytes] = []
        self.thread_ids: set[int] = set()
        self.concurrent_write = False
        self.complete = threading.Event()
        self._writing = threading.Lock()
        self._frames_lock = threading.Lock()

    def sendall(self, frame: bytes) -> None:
        if not self._writing.acquire(blocking=False):
            self.concurrent_write = True
            self._writing.acquire()
        try:
            time.sleep(0.001)
            with self._frames_lock:
                self.thread_ids.add(threading.get_ident())
                self.frames.append(frame)
                if len(self.frames) == self.expected:
                    self.complete.set()
        finally:
            self._writing.release()


class _TerminalHost:
    def __init__(self) -> None:
        self.waiter: Queue[bytes | None] = Queue()
        self.unsubscribed = threading.Event()

    def subscribe(self, terminal_id: str) -> Queue[bytes | None]:
        return self.waiter

    def unsubscribe(self, terminal_id: str, waiter: Queue[bytes | None]) -> None:
        self.unsubscribed.set()

    def write(self, terminal_id: str, payload: bytes) -> None:
        pass

    def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        pass


def _recv_frame(sock: socket.socket, buffer: bytearray) -> tuple[int, bytes]:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        decoded = decode_ws_frame(buffer)
        if decoded is not None:
            opcode, payload, consumed = decoded
            del buffer[:consumed]
            return opcode, payload
        buffer.extend(sock.recv(4096))
    raise AssertionError("timed out waiting for WebSocket frame")


class SerializedWebSocketSenderTests(unittest.TestCase):
    def test_multiple_producers_use_one_socket_writer(self) -> None:
        producer_count = 8
        frames_per_producer = 20
        frame_count = producer_count * frames_per_producer
        sock = _RecordingSocket(frame_count)
        stop = threading.Event()
        sender = _SerializedWebSocketSender(sock, stop, queue_size=frame_count)

        def produce(producer: int) -> None:
            for item in range(frames_per_producer):
                self.assertTrue(
                    sender.send(f"{producer}:{item}".encode(), opcode=2)
                )

        producers = [
            threading.Thread(target=produce, args=(producer,))
            for producer in range(producer_count)
        ]
        for producer in producers:
            producer.start()
        for producer in producers:
            producer.join(timeout=1)

        self.assertTrue(sock.complete.wait(timeout=2))
        sender.close()
        self.assertFalse(sock.concurrent_write)
        self.assertEqual(1, len(sock.thread_ids))
        self.assertEqual(frame_count, len(sock.frames))


class TerminalWebSocketHeartbeatTests(unittest.TestCase):
    def test_matching_pong_keeps_connection_until_next_ping_times_out(self) -> None:
        server_sock, client_sock = socket.socketpair()
        client_sock.settimeout(1.0)
        host = _TerminalHost()
        handler = SimpleNamespace(connection=server_sock)
        pump = threading.Thread(
            target=pump_terminal_socket,
            args=(handler, "term-heartbeat", host),
            kwargs={
                "heartbeat_interval_seconds": 0.03,
                "heartbeat_timeout_seconds": 0.12,
                "io_poll_seconds": 0.01,
                "send_timeout_seconds": 0.5,
            },
        )
        pump.start()
        buffer = bytearray()
        try:
            opcode, first_ping = _recv_frame(client_sock, buffer)
            self.assertEqual(9, opcode)
            client_sock.sendall(encode_ws_frame(first_ping, opcode=10))

            opcode, second_ping = _recv_frame(client_sock, buffer)
            self.assertEqual(9, opcode)
            self.assertNotEqual(first_ping, second_ping)
            self.assertTrue(pump.is_alive())

            pump.join(timeout=0.5)
            self.assertFalse(pump.is_alive())
            self.assertTrue(host.unsubscribed.is_set())
            opcode, close_payload = _recv_frame(client_sock, buffer)
            self.assertEqual(8, opcode)
            self.assertGreaterEqual(len(close_payload), 2)
            self.assertEqual(
                TERMINAL_WS_CLOSE_SERVICE_RESTART,
                int.from_bytes(close_payload[:2], "big"),
            )
        finally:
            client_sock.close()
            server_sock.close()
            pump.join(timeout=1)


class TerminalWebSocketCloseTests(unittest.TestCase):
    def test_pump_sends_service_restart_close_when_heartbeat_times_out(self) -> None:
        server_sock, client_sock = socket.socketpair()
        client_sock.settimeout(1.0)
        host = _TerminalHost()
        handler = SimpleNamespace(connection=server_sock)
        pump = threading.Thread(
            target=pump_terminal_socket,
            args=(handler, "term-close", host),
            kwargs={
                "heartbeat_interval_seconds": 0.03,
                "heartbeat_timeout_seconds": 0.08,
                "io_poll_seconds": 0.01,
                "send_timeout_seconds": 0.5,
            },
        )
        pump.start()
        buffer = bytearray()
        try:
            opcode, ping = _recv_frame(client_sock, buffer)
            self.assertEqual(9, opcode)
            self.assertTrue(ping)
            opcode, payload = _recv_frame(client_sock, buffer)
            self.assertEqual(8, opcode)
            self.assertGreaterEqual(len(payload), 2)
            self.assertEqual(
                TERMINAL_WS_CLOSE_SERVICE_RESTART,
                int.from_bytes(payload[:2], "big"),
            )
            pump.join(timeout=1)
            self.assertFalse(pump.is_alive())
            self.assertTrue(host.unsubscribed.is_set())
        finally:
            client_sock.close()
            server_sock.close()
            pump.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
