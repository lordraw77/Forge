"""A tiny SMTP server that keeps what it receives (`with SmtpSink() as sink:`)."""

from __future__ import annotations

import asyncio
import socket
import threading
from email import message_from_string
from email.message import Message
from typing import Any


class SmtpSink:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.port = 0

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def say(line: str) -> None:
            writer.write(f"{line}\r\n".encode())
            await writer.drain()

        await say("220 sink ready")
        recipients: list[str] = []
        while line := (await reader.readline()).decode(errors="replace").strip():
            cmd = line.split(" ", 1)[0].upper()
            if cmd in ("EHLO", "HELO", "MAIL", "RSET", "NOOP"):
                await say("250 ok")
            elif cmd == "RCPT":
                recipients.append(line.split(":", 1)[1].strip(" <>"))
                await say("250 ok")
            elif cmd == "DATA":
                await say("354 go on")
                lines: list[str] = []
                while (raw := (await reader.readline()).decode(errors="replace")) not in (
                    ".\r\n",
                    "",
                ):
                    lines.append(raw[1:] if raw.startswith("..") else raw)
                msg = message_from_string("".join(lines))
                msg["X-Rcpt"] = ",".join(recipients)
                self.messages.append(msg)
                recipients = []
                await say("250 queued")
            elif cmd == "QUIT":
                await say("221 bye")
                break
            else:
                await say("502 not implemented")
        writer.close()

    def __enter__(self) -> SmtpSink:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        ready = threading.Event()
        self._loop = asyncio.new_event_loop()

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            server = self._loop.run_until_complete(
                asyncio.start_server(self._client, "127.0.0.1", self.port)
            )
            self._server = server
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        assert ready.wait(10)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def to(self, address: str) -> list[Message]:
        return [m for m in self.messages if address in m["X-Rcpt"].split(",")]
