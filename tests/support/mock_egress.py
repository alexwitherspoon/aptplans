"""Deterministic in-process HTTP proxy for egress contract tests."""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlparse


@dataclass
class MockEgress:
    host: str = "127.0.0.1"
    port: int = 0
    responses: dict[str, tuple[int, bytes]] = field(default_factory=dict)
    connect_hosts: list[str] = field(default_factory=list)
    proxy_requests: list[str] = field(default_factory=list)
    accept_connections: bool = True
    reject_connect: bool = False
    _server: socket.socket | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    @property
    def url(self) -> str:
        if self.port <= 0:
            raise RuntimeError("mock egress is not started")
        return f"http://{self.host}:{self.port}"

    def set_response(self, url: str, body: bytes, *, status: int = 200) -> None:
        self.responses[url] = (status, body)

    def start(self) -> str:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.url

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

    def _serve(self) -> None:
        assert self._server is not None
        self._server.settimeout(0.5)
        while True:
            try:
                conn, _addr = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            if not self.accept_connections:
                conn.close()
                return
            request = self._read_headers(conn)
            if not request:
                return
            line = request.splitlines()[0]
            self.proxy_requests.append(line)
            if line.startswith("CONNECT "):
                target = line.split()[1]
                host = target.rsplit(":", 1)[0]
                self.connect_hosts.append(host)
                if self.reject_connect:
                    conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                return
            if line.startswith(("GET ", "POST ", "HEAD ")):
                target = line.split()[1]
                parsed = urlparse(target)
                key = parsed.geturl()
                status, body = self.responses.get(key, (404, b"not found"))
                header = (
                    f"HTTP/1.1 {status} OK\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                conn.sendall(header + body)
        finally:
            conn.close()

    @staticmethod
    def _read_headers(conn: socket.socket) -> str:
        chunks: list[bytes] = []
        while b"\r\n\r\n" not in b"".join(chunks):
            data = conn.recv(4096)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("latin-1", errors="replace")


@contextmanager
def start_mock_egress(**kwargs) -> Iterator[MockEgress]:
    mock = MockEgress(**kwargs)
    mock.start()
    try:
        yield mock
    finally:
        mock.close()
