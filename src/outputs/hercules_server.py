"""TCP listener for Hercules clients (this app is the server; Hercules connects as a client).

Protocol (all strings configurable in SETUP → Hercules):
    isready         -> readyyes | readyno
    scanjobno       -> <decoded 2D number> | noread
    threadpresence  -> threadok | threadnok         (and a PDF report is written)
    anything else   -> error
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger("hercules")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "bind_host": "0.0.0.0",
    "port": 9000,
    "ready_expect": "isready",
    "ready_reply": "readyyes",
    "not_ready_reply": "readyno",
    "scan_expect": "scanjobno",
    "scan_fail_reply": "noread",
    "thread_expect": "threadpresence",
    "thread_ok_reply": "threadok",
    "thread_nok_reply": "threadnok",
    "error_reply": "error",
}
_STRING_KEYS = [k for k, v in DEFAULT_CONFIG.items() if isinstance(v, str) and k != "bind_host"]

# Handlers run in a worker thread. ready() -> bool; scan()/thread() -> (reply, result-dict).
ReadyFn = Callable[[], bool]
StepFn = Callable[[], tuple[str, dict]]


def normalize_config(data: dict | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    for key, value in (data or {}).items():
        if key not in cfg:
            raise ValueError(f"Unknown Hercules setting: {key}")
        cfg[key] = value
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["bind_host"] = str(cfg["bind_host"]).strip() or "0.0.0.0"
    try:
        cfg["port"] = int(cfg["port"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Port must be a number") from exc
    if not 1 <= cfg["port"] <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    for key in _STRING_KEYS:
        cfg[key] = str(cfg[key]).strip()
        if not cfg[key]:
            raise ValueError(f"'{key}' must not be empty")
    commands = [cfg["ready_expect"].casefold(), cfg["scan_expect"].casefold(), cfg["thread_expect"].casefold()]
    if len(set(commands)) != 3:
        raise ValueError("The three expected commands must be different")
    return cfg


@dataclass
class HerculesEvent:
    kind: str
    message: str
    reply: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    result: Optional[dict] = None
    seq: int = 0


class HerculesServer:
    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None
        self.config = normalize_config({})
        self.listening = False
        self.client_addr: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_event: Optional[HerculesEvent] = None
        self._seq = 0
        self._busy = asyncio.Lock()
        self._ready_fn: Optional[ReadyFn] = None
        self._scan_fn: Optional[StepFn] = None
        self._thread_fn: Optional[StepFn] = None

    def set_handlers(self, *, ready: ReadyFn, scan: StepFn, thread: StepFn) -> None:
        self._ready_fn, self._scan_fn, self._thread_fn = ready, scan, thread

    def status(self) -> dict:
        return {
            "enabled": self.config["enabled"],
            "listening": self.listening,
            "bind_host": self.config["bind_host"],
            "port": self.config["port"],
            "client_connected": self.client_addr is not None,
            "client_addr": self.client_addr,
            "last_error": self.last_error,
            "last_event": asdict(self.last_event) if self.last_event else None,
        }

    def _record(self, event: HerculesEvent) -> None:
        self._seq += 1
        event.seq = self._seq
        self.last_event = event

    async def apply_config(self, config: dict) -> dict:
        self.config = normalize_config(config)
        await self.stop()
        if self.config["enabled"]:
            await self.start()
        return self.config

    async def start(self) -> None:
        host, port = self.config["bind_host"], self.config["port"]
        try:
            self._server = await asyncio.start_server(self._handle_client, host, port)
        except OSError as exc:
            self.last_error = f"Listen failed on {host}:{port}: {exc}"
            self.listening = False
            self._server = None
            log.error(self.last_error)
            return
        self.listening = True
        self.last_error = None
        log.info("Hercules TCP server listening on %s:%s", host, port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=2)
            except Exception:
                pass
            self._server = None
        self.listening = False
        self.client_addr = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        addr = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        self.client_addr = addr
        self._record(HerculesEvent(kind="connect", message=f"Client {addr}"))
        try:
            while True:
                message = await self._read_message(reader)
                if message is None:
                    break
                reply = await self.dispatch(message)
                if reply is not None:
                    writer.write((reply + "\r\n").encode("ascii", errors="ignore"))
                    await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network errors must not kill the listener
            self.last_error = str(exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if self.client_addr == addr:
                self.client_addr = None
            self._record(HerculesEvent(kind="disconnect", message=f"Client {addr}"))

    @staticmethod
    async def _read_message(reader: asyncio.StreamReader) -> Optional[str]:
        """One message: newline-terminated, or a short burst without newline (Hercules 'Send')."""
        buf = bytearray()
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(256), timeout=None if not buf else 0.15)
            except asyncio.TimeoutError:
                break
            if not chunk:
                if buf:
                    break
                return None
            buf.extend(chunk)
            if b"\n" in buf or b"\r" in buf:
                break
        text = bytes(buf).decode("utf-8", errors="ignore").replace("\r", "\n").split("\n", 1)[0].strip()
        return text if text else ""

    async def dispatch(self, raw: str) -> Optional[str]:
        token = raw.strip()
        if not token:
            return None
        cfg = self.config
        command = token.casefold()
        async with self._busy:  # one inspection at a time
            try:
                if command == cfg["ready_expect"].casefold():
                    ready = bool(self._ready_fn and await asyncio.to_thread(self._ready_fn))
                    reply = cfg["ready_reply"] if ready else cfg["not_ready_reply"]
                    self._record(HerculesEvent(kind="ready", message=token, reply=reply))
                    return reply
                if command == cfg["scan_expect"].casefold() and self._scan_fn:
                    reply, result = await asyncio.to_thread(self._scan_fn)
                    self._record(HerculesEvent(kind="scan", message=token, reply=reply, result=result))
                    return reply
                if command == cfg["thread_expect"].casefold() and self._thread_fn:
                    reply, result = await asyncio.to_thread(self._thread_fn)
                    self._record(HerculesEvent(kind="thread", message=token, reply=reply, result=result))
                    return reply
            except Exception as exc:
                log.warning("Hercules command %r failed: %s", token, exc)
                self.last_error = f"{token}: {exc}"
                self._record(HerculesEvent(kind="error", message=token, reply=cfg["error_reply"]))
                return cfg["error_reply"]
        self._record(HerculesEvent(kind="error", message=f"Unknown command '{token}'", reply=cfg["error_reply"]))
        return cfg["error_reply"]
