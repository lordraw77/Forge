"""Prompt templates: `{name}` shorthand over sandboxed Jinja2 with resource limits
(US-1.10.1 #1 and #4, threat model "Jinja2 template injection in prompts").

The spec writes variables as `{name}`; full Jinja2 (`{{ name }}`, `{% if %}`, `{% for %}`) is
also accepted. Templates come from project builders, so rendering assumes hostile input:

- `ImmutableSandboxedEnvironment` with no loader: no imports, includes or filesystem;
- only allowlisted filters, globals, methods and statement types;
- `range()` capped, `*` / `**` / `+` results capped;
- a per-line trace on the compiled template enforces a step and time budget and aborts as soon
  as any intermediate string or list grows past the output cap (blocks doubling tricks).
"""

from __future__ import annotations

import functools
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from types import FrameType
from typing import Any

from jinja2 import ChainableUndefined, Template, TemplateSyntaxError, Undefined, meta, nodes
from jinja2.exceptions import SecurityError
from jinja2.exceptions import TemplateError as JinjaTemplateError
from jinja2.filters import FILTERS
from jinja2.sandbox import ImmutableSandboxedEnvironment

MAX_TEMPLATE_CHARS = 32_000
MAX_VALUE_CHARS = 262_144  # per variable on the test page and playground only
MAX_OUTPUT_CHARS = 1_000_000  # growth a template may add on top of its inputs
# Per render: the output and intermediate-value cap = MAX_OUTPUT_CHARS + the size of the inputs,
# so large inputs in production use are never refused while doubling tricks still are.
_SIZE_CAP: ContextVar[int] = ContextVar("template_size_cap", default=MAX_OUTPUT_CHARS)
MAX_RANGE = 1_000
MAX_INT_BITS = 4_096
MAX_STEPS = 200_000
RENDER_BUDGET_S = 1.0

# `{name}` not part of `{{ … }}`; `{%` and `{#` never match because they do not start a name.
_SHORTHAND = re.compile(r"(?<!\{)\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}(?!\})")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_TEMPLATE_FILE = "<template>"

_FILTERS = [
    "abs",
    "capitalize",
    "count",
    "d",
    "default",
    "dictsort",
    "e",
    "escape",
    "first",
    "float",
    "forceescape",
    "groupby",
    "int",
    "items",
    "join",
    "last",
    "length",
    "list",
    "lower",
    "map",
    "max",
    "min",
    "reject",
    "rejectattr",
    "reverse",
    "round",
    "safe",
    "select",
    "selectattr",
    "sort",
    "string",
    "striptags",
    "sum",
    "title",
    "tojson",
    "trim",
    "truncate",
    "unique",
    "upper",
    "urlencode",
    "wordcount",
    "wordwrap",
]
_METHODS = frozenset(
    [
        "items",
        "keys",
        "values",
        "get",
        "lower",
        "upper",
        "title",
        "capitalize",
        "strip",
        "lstrip",
        "rstrip",
        "split",
        "splitlines",
        "startswith",
        "endswith",
        "count",
        "find",
        "isdigit",
        "isalpha",
        "isalnum",
        "isspace",
    ]
)
# Statements a prompt needs; macros, call blocks, imports, includes and inheritance are refused.
_FORBIDDEN_NODES: tuple[type[nodes.Node], ...] = (
    nodes.Macro,
    nodes.CallBlock,
    nodes.Import,
    nodes.FromImport,
    nodes.Include,
    nodes.Extends,
    nodes.Block,
    nodes.FilterBlock,
    nodes.With,
    nodes.ScopedEvalContextModifier,
)


class TemplateError(Exception):
    """A template that cannot be parsed or rendered; `line` is 1-based when known."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


def normalize(source: str) -> str:
    """Rewrite the `{name}` shorthand to `{{ name }}`."""
    return _SHORTHAND.sub(r"{{ \1 }}", source)


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name))


def _too_big(value: Any) -> bool:
    if isinstance(value, int):
        return value.bit_length() > MAX_INT_BITS
    return isinstance(value, str | list | tuple | dict) and len(value) > _SIZE_CAP.get()


def _safe_range(*args: int) -> range:
    rng = range(*args)
    if len(rng) > MAX_RANGE:
        raise SecurityError(f"range() is limited to {MAX_RANGE} items")
    return rng


def _safe_replace(s: Any, old: str, new: str, count: int | None = None) -> str:
    text = str(s)
    hits = text.count(old) if old else len(text) + 1
    if count is not None and count >= 0:
        hits = min(hits, count)
    if len(text) + hits * max(len(new) - len(old), 0) > _SIZE_CAP.get():
        raise SecurityError("replace() result is too large")
    return text.replace(old, new, -1 if count is None else count)


class _Sandbox(ImmutableSandboxedEnvironment):
    intercepted_binops = frozenset({"*", "**", "+"})

    def call_binop(self, context: Any, op: str, left: Any, right: Any) -> Any:
        if op == "**" and isinstance(right, int | float):
            base_bits = left.bit_length() if isinstance(left, int) else 1
            if abs(right) > 64 or base_bits * abs(right) > MAX_INT_BITS:
                raise SecurityError("power result is too large")
        if op == "*":
            ints = isinstance(left, int) and isinstance(right, int)
            if ints and left.bit_length() + right.bit_length() > MAX_INT_BITS:
                raise SecurityError("product is too large")
            for seq, n in ((left, right), (right, left)):
                sized = isinstance(seq, str | list | tuple) and isinstance(n, int)
                if sized and len(seq) * n > _SIZE_CAP.get():
                    raise SecurityError("repetition result is too large")
        sequences = isinstance(left, str | list | tuple) and isinstance(right, type(left))
        if op == "+" and sequences and len(left) + len(right) > _SIZE_CAP.get():
            raise SecurityError("concatenation result is too large")
        return super().call_binop(context, op, left, right)

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        if not super().is_safe_attribute(obj, attr, value):
            return False
        # Chained access on an absent optional variable stays undefined (renders as "").
        return isinstance(value, Undefined) or not callable(value) or attr in _METHODS

    def getattr(self, obj: Any, attribute: str) -> Any:
        # Also on undefined values, which would otherwise swallow `__globals__` and friends.
        if attribute.startswith("_"):
            raise SecurityError(f"access to attribute {attribute!r} is not allowed")
        return super().getattr(obj, attribute)

    def wrap_str_format(self, value: Any) -> None:
        # `str.format` would get a sandboxed formatter that still honours huge widths.
        return None

    def unsafe_undefined(self, obj: Any, attribute: str) -> Any:
        # The default returns an Undefined that ChainableUndefined would render as "".
        raise SecurityError(f"access to attribute {attribute!r} is not allowed")


def _environment() -> _Sandbox:
    env = _Sandbox(
        autoescape=False,
        undefined=ChainableUndefined,
        keep_trailing_newline=True,
        loader=None,
    )
    env.filters = {name: FILTERS[name] for name in _FILTERS}
    env.filters["replace"] = _safe_replace
    env.globals = {"range": _safe_range, "dict": dict}
    return env


_ENV = _environment()


def _parse(source: str) -> nodes.Template:
    if len(source) > MAX_TEMPLATE_CHARS:
        raise TemplateError(f"Template exceeds {MAX_TEMPLATE_CHARS} characters")
    try:
        ast = _ENV.parse(normalize(source))
    except TemplateSyntaxError as exc:
        raise TemplateError(exc.message or "Syntax error", exc.lineno) from exc
    for node in ast.find_all(_FORBIDDEN_NODES):
        kind = type(node).__name__.lower()
        raise TemplateError(f"'{kind}' is not allowed in prompt templates", node.lineno)
    return ast


@functools.lru_cache(maxsize=256)
def _compile(source: str) -> Template:
    """Compiled templates are cached by source: prompt versions are immutable."""
    ast = _parse(source)
    try:
        return _ENV.from_string(ast)
    except TemplateSyntaxError as exc:  # also unknown filters and tests
        raise TemplateError(exc.message or "Syntax error", exc.lineno) from exc


def variables(*sources: str) -> list[str]:
    """Variables the templates read, in order of first appearance (raises TemplateError)."""
    seen: list[str] = []
    for source in sources:
        ast = _parse(source)
        free = meta.find_undeclared_variables(ast)
        for name in ast.find_all(nodes.Name):
            if name.ctx == "load" and name.name in free and name.name not in seen:
                seen.append(name.name)
    return [v for v in seen if v not in _ENV.globals]


def check(source: str) -> None:
    """Parse and compile without rendering (raises TemplateError)."""
    _compile(source)


@dataclass
class _Budget:
    deadline: float
    steps: int = 0

    def local(self, frame: FrameType, _event: str, _arg: Any) -> Callable[..., Any]:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise SecurityError("template exceeds the step budget")
        if self.steps % 256 == 0 and time.monotonic() > self.deadline:
            raise SecurityError("template exceeds the time budget")
        if any(_too_big(v) for v in frame.f_locals.values()):
            raise SecurityError("template builds a value that is too large")
        return self.local

    def tracer(self, frame: FrameType, _event: str, _arg: Any) -> Callable[..., Any] | None:
        return self.local if frame.f_code.co_filename == _TEMPLATE_FILE else None


def render(source: str, values: Mapping[str, Any], *, limit_values: bool = True) -> str:
    """Render one template with `values` under the sandbox and budgets.

    Synchronous and CPU-bound: call it through `asyncio.to_thread`. The trace function is
    per-thread, so it never sees other requests' code.
    """
    inputs = 0
    for name, value in values.items():
        size = len(value) if isinstance(value, str | list | tuple | dict) else 0
        if limit_values and size > MAX_VALUE_CHARS:
            raise TemplateError(f"Value of '{name}' exceeds {MAX_VALUE_CHARS} characters")
        inputs += size
    template = _compile(source)
    token = _SIZE_CAP.set(MAX_OUTPUT_CHARS + 2 * inputs)
    budget = _Budget(deadline=time.monotonic() + RENDER_BUDGET_S)
    previous = sys.gettrace()
    sys.settrace(budget.tracer)
    try:
        parts: list[str] = []
        size = 0
        for chunk in template.generate(dict(values)):
            size += len(chunk)
            if size > _SIZE_CAP.get():
                raise SecurityError("rendered prompt is too large")
            parts.append(chunk)
        return "".join(parts)
    except SecurityError as exc:
        raise TemplateError(f"Blocked by the template sandbox: {exc}") from exc
    except JinjaTemplateError as exc:
        raise TemplateError(str(exc) or type(exc).__name__, getattr(exc, "lineno", None)) from exc
    except (TypeError, ValueError, ArithmeticError, LookupError) as exc:
        raise TemplateError(f"Render error: {exc}") from exc
    finally:
        sys.settrace(previous)
        _SIZE_CAP.reset(token)


def missing_required(required: Iterable[str], values: Mapping[str, Any]) -> list[str]:
    return [n for n in required if not str(values.get(n) or "").strip()]


__all__ = [
    "MAX_TEMPLATE_CHARS",
    "MAX_VALUE_CHARS",
    "TemplateError",
    "check",
    "missing_required",
    "normalize",
    "render",
    "valid_name",
    "variables",
]
