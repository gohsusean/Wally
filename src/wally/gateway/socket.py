"""Local Unix socket for one Gateway process. Not a network listener."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from wally.gateway.service import GatewayResult, GatewayRuntime

_MAX_MESSAGE = 65_536


class GatewaySocket:
    """Accept line-delimited JSON calls and dispatch them to the runtime."""

    def __init__(self, runtime: GatewayRuntime, path: Path) -> None:
        self._runtime = runtime
        self._path = path
        if path.exists():
            path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="wally-gateway", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)
        if self._path.exists():
            self._path.unlink()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                self._handle(connection)

    def _handle(self, connection: socket.socket) -> None:
        raw = _read_line(connection)
        try:
            message = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            _write(connection, GatewayResult(ok=False, error="Request body must be an object."))
            return
        if not isinstance(message, dict):
            _write(connection, GatewayResult(ok=False, error="Request body must be an object."))
            return
        body = message.get("body", {})
        result = self._runtime.dispatch(
            adapter_id=str(message.get("adapter_id") or ""),
            credential=str(message.get("credential") or ""),
            op=str(message.get("op") or ""),
            body=body if isinstance(body, dict) else None,
        )
        _write(connection, result)


def call_gateway(path: Path, message: dict) -> dict:
    """Send one call and return the runtime's response. Credentials are not echoed."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(path))
        sock.sendall(json.dumps(message).encode() + b"\n")
        data = json.loads(_read_line(sock) or "{}")
    return data if isinstance(data, dict) else {"ok": False, "error": "Bad gateway response."}


def _read_line(connection: socket.socket) -> str:
    chunks: list[bytes] = []
    size = 0
    while b"\n" not in b"".join(chunks):
        part = connection.recv(4096)
        if not part:
            break
        size += len(part)
        if size > _MAX_MESSAGE:
            break
        chunks.append(part)
    return b"".join(chunks).split(b"\n", 1)[0].decode()


def _write(connection: socket.socket, result: GatewayResult) -> None:
    connection.sendall(json.dumps(result.as_dict()).encode() + b"\n")
