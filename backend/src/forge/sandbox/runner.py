"""Runs inside the sandbox container (image `forge-sandbox-runtime`): standard library only.

The payload comes in the environment variable FORGE_PAYLOAD (base64 of JSON: code, args, entry).
The code defines `run(args)`; its JSON-serialisable return value is the result. Anything the code
prints is captured. The result goes to the real stdout as the last line, after a marker; the
process then exits without running anything else, so the code cannot append lines after it.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
import traceback
from typing import Any

MARKER = "\x1eFORGE_RESULT\x1e"
CAPTURE_LIMIT = 64 * 1024


class _Limited(io.StringIO):
    def write(self, s: str) -> int:
        room = CAPTURE_LIMIT - self.tell()
        if room > 0:
            super().write(s[:room])
        return len(s)


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    out, err = _Limited(), _Limited()
    result: dict[str, Any] = {"ok": True}
    try:
        namespace: dict[str, Any] = {"__name__": "__sandbox__"}
        code = compile(payload["code"], "<code>", "exec")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(code, namespace)  # noqa: S102 - the point of the sandbox
            entry = namespace.get(payload.get("entry", "run"))
            if not callable(entry):
                raise NameError(
                    f"the code must define a function {payload.get('entry', 'run')}(args)"
                )
            value = entry(payload.get("args") or {})
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"the result is not JSON-serialisable: {exc}") from exc
        result["result"] = value
    except BaseException as exc:
        result = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "traceback": "".join(traceback.format_exception_only(type(exc), exc))[:2000],
        }
        if isinstance(exc, SyntaxError):
            result["error"] = f"SyntaxError: {exc.msg} (line {exc.lineno})"
    result["stdout"], result["stderr"] = out.getvalue(), err.getvalue()
    return result


def main() -> None:
    raw = os.environ.pop("FORGE_PAYLOAD", "")
    try:
        payload = json.loads(base64.b64decode(raw))
    except ValueError:
        result = {"ok": False, "error": "invalid payload", "stdout": "", "stderr": ""}
    else:
        result = execute(payload)
    line = json.dumps(result, ensure_ascii=False)
    sys.stdout.write("\n" + MARKER + line + "\n")
    sys.stdout.flush()
    os._exit(0)  # nothing registered by the code (atexit, threads) runs after the result


if __name__ == "__main__":
    main()
