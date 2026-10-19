"""An MCP server over Streamable HTTP for tool tests (`with MockMcp() as url:`)."""

from __future__ import annotations

import socket
import threading
import time

import uvicorn
from mcp.server.mcpserver import MCPServer


def build() -> MCPServer:
    server = MCPServer("forge-test")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool()
    def echo(text: str) -> str:
        """Echo the text."""
        return f"echo: {text}"

    @server.tool()
    def boom() -> str:
        """Always fails."""
        raise ValueError("boom")

    return server


class MockMcp:
    def __enter__(self) -> str:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self._server = uvicorn.Server(
            uvicorn.Config(
                build().streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        return f"http://127.0.0.1:{port}/mcp"

    def __exit__(self, *_exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
