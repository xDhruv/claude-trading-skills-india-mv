"""Minimal MCP client for Streamable-HTTP servers that need no authentication."""
import json
import time
import urllib.error
import urllib.request

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "User-Agent": "nse-agent/1.0",
}


class McpError(Exception):
    pass


def _decode(body: str) -> dict:
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    last = ""
    for line in body.splitlines():  # server-sent events: take the last data frame
        if line.startswith("data:"):
            last = line[5:].strip()
    return json.loads(last)


class McpClient:
    def __init__(self, url: str, timeout: float = 30, retries: int = 2, pause: float = 0.25):
        self.url, self.timeout, self.retries, self.pause = url, timeout, retries, pause
        self.session_id: str | None = None
        self._initialized = False
        self._id = 0

    def _post(self, payload: dict) -> dict:
        headers = dict(HEADERS)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, json.dumps(payload).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            body = resp.read().decode("utf-8")
        return _decode(body) if body.strip() else {}

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                out = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
                if "error" in out:
                    raise McpError(f"{method}: {out['error']}")
                return out.get("result", {})
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
        raise McpError(f"{method} failed after retries: {last}")

    def _init(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        try:
            self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                     "clientInfo": {"name": "nse-agent", "version": "1.0"}})
        except McpError:
            pass  # these servers also answer without a session

    def call(self, tool: str, args: dict | None = None):
        """Call a tool and return its parsed JSON (or a number / raw text if it is not JSON)."""
        self._init()
        result = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise McpError(f"{tool}: {text[:200]}")
        time.sleep(self.pause)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                return float(text)
            except ValueError:
                return text
