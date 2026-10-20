"""The guardrail engine (E2.11, ADR-013): a policy is a list of rules; each rule runs one check on
a stage's text and answers with findings; the strictest action wins.

Checks that need no model run in-process (PII patterns, secrets, URLs, banned topics, JSON schema,
injection heuristics). Checks that need a guard or judge model (toxicity, grounding, model-based
injection) are `unavailable` until an endpoint is configured; the rule's `fail_mode` decides what
that means (default: closed for `block` rules).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import jsonschema

from forge.guardrails.injection.detectors import heuristic as injection_heuristic

Stage = Literal["input", "output", "tool_args"]
Action = Literal["log", "mask", "block", "rewrite", "escalate"]
CheckType = Literal[
    "pii", "injection", "secrets", "urls", "banned_topics", "json_schema", "toxicity", "grounding"
]

STAGES: tuple[str, ...] = ("input", "output", "tool_args")
ACTIONS: tuple[str, ...] = ("log", "mask", "block", "rewrite", "escalate")
CHECK_TYPES: tuple[str, ...] = (
    "pii",
    "injection",
    "secrets",
    "urls",
    "banned_topics",
    "json_schema",
    "toxicity",
    "grounding",
)
# Checks that cannot run without a model endpoint (ADR-013).
MODEL_CHECKS = frozenset({"toxicity", "grounding"})
# Strictness order: the verdict takes the highest action reached.
_RANK = {"log": 0, "mask": 1, "rewrite": 1, "escalate": 2, "block": 3}


@dataclass(frozen=True)
class Rule:
    type: str
    stage: tuple[str, ...] = ("input", "output")
    action: str = "log"
    params: dict[str, Any] = field(default_factory=dict)
    fail_mode: str | None = None  # "open" | "closed"; None = closed for block/escalate rules

    def closed(self) -> bool:
        """Fail closed when the check cannot run. Default: every rule that changes or stops text;
        only `log` fails open."""
        default = "open" if self.action == "log" else "closed"
        return (self.fail_mode or default) == "closed"


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    label: str


@dataclass(frozen=True)
class Finding:
    rule: int
    type: str
    action: str
    label: str
    detail: str = ""
    spans: tuple[Span, ...] = ()


@dataclass
class Verdict:
    text: str  # the text after masking and rewriting
    action: str = "allow"  # allow | log | mask | rewrite | escalate | block
    findings: list[Finding] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action in ("block", "escalate")


# ----------------------------------------------------------------------------- PII
def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if flip:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
        flip = not flip
    return total % 10 == 0


def _iban(value: str) -> bool:
    flat = re.sub(r"\s", "", value).upper()
    if not 15 <= len(flat) <= 34:
        return False
    rearranged = flat[4:] + flat[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return int(number) % 97 == 1


_CF_ODD = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            2,
            4,
            18,
            20,
            11,
            3,
            6,
            8,
            12,
            14,
            16,
            10,
            22,
            25,
            24,
            23,
        ],
        strict=True,
    )
)


def _codice_fiscale(value: str) -> bool:
    cf = value.upper()
    total = 0
    for i, ch in enumerate(cf[:15]):
        if i % 2 == 0:
            total += _CF_ODD[ch]
        else:
            total += int(ch) if ch.isdigit() else ord(ch) - ord("A")
    return chr(ord("A") + total % 26) == cf[15]


# entity -> (pattern, validator)
PII_PATTERNS: dict[str, tuple[re.Pattern[str], Callable[[str], bool] | None]] = {
    "EMAIL": (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), None),
    "CREDIT_CARD": (
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        lambda v: 13 <= len(re.sub(r"\D", "", v)) <= 19 and _luhn(re.sub(r"\D", "", v)),
    ),
    "IBAN": (
        re.compile(r"\b[A-Za-z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{4}){2,7}(?:[ ]?[A-Za-z0-9]{1,4})?\b"),
        _iban,
    ),
    "IT_FISCAL_CODE": (
        re.compile(r"\b[A-Za-z]{6}\d{2}[A-Za-z]\d{2}[A-Za-z]\d{3}[A-Za-z]\b"),
        _codice_fiscale,
    ),
    "PHONE": (
        re.compile(r"(?<!\w)(?:\+|00)\d{1,3}[ .-]?\d{2,4}(?:[ .-]?\d{2,4}){2,3}(?!\w)"),
        None,
    ),
    "IP_ADDRESS": (
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
        None,
    ),
}
DEFAULT_PII = ("EMAIL", "CREDIT_CARD", "IBAN", "IT_FISCAL_CODE", "PHONE")
# Entities found by named-entity recognition (Presidio + spaCy, `guardrails.ner`); never default.
NER_ENTITIES = ("PERSON", "LOCATION", "ORGANIZATION")


def check_pii(text: str, params: dict[str, Any]) -> list[Span]:
    entities = params.get("entities") or DEFAULT_PII
    spans: list[Span] = []
    for entity in entities:
        if entity not in PII_PATTERNS:
            continue
        pattern, valid = PII_PATTERNS[entity]
        for m in pattern.finditer(text):
            if valid is None or valid(m.group()):
                spans.append(Span(m.start(), m.end(), entity))
    return _merge(spans)


def _merge(spans: list[Span]) -> list[Span]:
    """Overlapping matches (a card number that also looks like a phone) keep the longest."""
    out: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -(s.end - s.start))):
        if out and span.start < out[-1].end:
            continue
        out.append(span)
    return out


# ------------------------------------------------------------------------- secrets
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "AWS_ACCESS_KEY": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "PRIVATE_KEY": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "GITHUB_TOKEN": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "SLACK_TOKEN": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "API_KEY": re.compile(r"\b(?:sk|fk|fap)[-_][A-Za-z0-9_-]{20,}\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "PASSWORD_ASSIGN": re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token)\s*[=:]\s*['\"]?[^\s'\"]{6,}"
    ),
}


def check_secrets(text: str, params: dict[str, Any]) -> list[Span]:
    spans = [
        Span(m.start(), m.end(), name)
        for name, pat in SECRET_PATTERNS.items()
        for m in pat.finditer(text)
    ]
    for extra in params.get("patterns") or []:
        try:
            spans += [Span(m.start(), m.end(), "CUSTOM") for m in re.finditer(extra, text)]
        except re.error:
            continue
    return _merge(spans)


# ---------------------------------------------------------------------------- URLs
URL = re.compile(r"\bhttps?://[^\s<>\"')]+", re.IGNORECASE)
_SHORTENERS = frozenset({"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "cutt.ly"})


def _host_match(host: str, patterns: list[str]) -> bool:
    return any(host == p or host.endswith("." + p.lstrip("*.")) for p in patterns)


def check_urls(text: str, params: dict[str, Any]) -> list[Span]:
    allow = [h.lower() for h in params.get("allow") or []]
    deny = [h.lower() for h in params.get("deny") or []]
    spans: list[Span] = []
    for m in URL.finditer(text):
        host = (urlsplit(m.group()).hostname or "").lower()
        if not host:
            continue
        bad = (
            _host_match(host, deny)
            or (allow and not _host_match(host, allow))
            or host in _SHORTENERS
            or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host) is not None
        )
        if bad:
            spans.append(Span(m.start(), m.end(), "URL"))
    return spans


# ------------------------------------------------------------------- banned topics
def check_banned(text: str, params: dict[str, Any]) -> list[Span]:
    spans: list[Span] = []
    for term in params.get("terms") or []:
        if not term:
            continue
        for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE):
            spans.append(Span(m.start(), m.end(), term))
    return _merge(spans)


# ----------------------------------------------------------------------- JSON schema
def check_json_schema(text: str, params: dict[str, Any]) -> str | None:
    """None when `text` is JSON that matches the schema, otherwise the reason."""
    schema = params.get("schema")
    if not isinstance(schema, dict):
        return "no schema configured"
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", body).strip()
    try:
        data = json.loads(body)
    except ValueError as exc:
        return f"not valid JSON: {exc}"
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        return exc.message[:300]
    except jsonschema.SchemaError as exc:
        return f"invalid schema: {exc.message[:200]}"
    return None


# --------------------------------------------------------------------------- engine
def mask_text(text: str, spans: list[Span], replacement: str | None = None) -> str:
    out, pos = [], 0
    for span in sorted(spans, key=lambda s: s.start):
        out.append(text[pos : span.start])
        out.append(replacement if replacement is not None else f"[{span.label}]")
        pos = span.end
    out.append(text[pos:])
    return "".join(out)


def evaluate(
    text: str,
    stage: str,
    rules: list[Rule],
    *,
    models: dict[str, Callable[..., Any]] | None = None,
) -> Verdict:
    """Run every rule of `stage` on `text`.

    `models` maps a check type to a callable backed by a configured guard or judge endpoint; a
    model check without one is recorded as unavailable and, for closed rules, blocks.
    """
    verdict = Verdict(text)
    models = models or {}
    for index, rule in enumerate(rules):
        if stage not in rule.stage:
            continue
        spans: list[Span] = []
        flagged, detail = False, ""
        runner = models.get(rule.type)
        if rule.type == "pii":
            spans = check_pii(verdict.text, rule.params)
            wanted = [e for e in rule.params.get("entities") or () if e in NER_ENTITIES]
            if wanted:
                found = (
                    models["ner"](verdict.text, wanted, rule.params.get("languages"))
                    if "ner" in models
                    else None
                )
                if found is None:  # Presidio or its models are missing
                    verdict.unavailable.append("ner")
                    if rule.closed():
                        verdict.findings.append(
                            Finding(index, rule.type, "block", "unavailable", "no NER models")
                        )
                        verdict.action = _stronger(verdict.action, "block")
                else:
                    spans = _merge(spans + found)
            flagged = bool(spans)
        elif rule.type == "secrets":
            spans = check_secrets(verdict.text, rule.params)
            flagged = bool(spans)
        elif rule.type == "urls":
            spans = check_urls(verdict.text, rule.params)
            flagged = bool(spans)
        elif rule.type == "banned_topics":
            spans = check_banned(verdict.text, rule.params)
            flagged = bool(spans)
        elif rule.type == "json_schema":
            reason = check_json_schema(verdict.text, rule.params)
            flagged, detail = reason is not None, reason or ""
        elif rule.type == "injection":
            flagged = injection_heuristic(verdict.text)
            if not flagged and runner:
                got = runner(verdict.text, rule)
                if got is None:
                    _cannot_run(verdict, index, rule, "guard model")
                else:
                    flagged = bool(got)
        elif rule.type in MODEL_CHECKS:
            result = runner(verdict.text, rule) if runner else None
            if result is None:
                verdict.unavailable.append(rule.type)
                _cannot_run(verdict, index, rule, "guard endpoint", note=False)
                continue
            spans = result if isinstance(result, list) else []
            flagged = bool(result)
        else:
            continue
        if not flagged:
            continue
        # `mask` and `rewrite` need spans; without them (injection, schema) there is nothing to
        # cut out, so the rule blocks, unless the operator marked it fail-open (then it logs).
        action = rule.action
        if action in ("mask", "rewrite") and not spans:
            action = "log" if rule.fail_mode == "open" else "block"
        label = ",".join(sorted({s.label for s in spans})) or rule.type
        verdict.findings.append(Finding(index, rule.type, action, label, detail, tuple(spans)))
        if action == "mask":
            verdict.text = mask_text(verdict.text, spans)
        elif action == "rewrite":
            verdict.text = mask_text(verdict.text, spans, str(rule.params.get("replacement", "…")))
        verdict.action = _stronger(verdict.action, action)
    return verdict


def _cannot_run(verdict: Verdict, index: int, rule: Rule, what: str, *, note: bool = True) -> None:
    """A check that needed a model and got none: closed rules block, the rest let the text pass."""
    if note and rule.type not in verdict.unavailable:
        verdict.unavailable.append(rule.type)
    if rule.closed():
        verdict.findings.append(Finding(index, rule.type, "block", "unavailable", f"no {what}"))
        verdict.action = _stronger(verdict.action, "block")


def _stronger(current: str, new: str) -> str:
    if current == "allow":
        return new
    return new if _RANK[new] > _RANK[current] else current


def parse_rules(raw: list[dict[str, Any]]) -> list[Rule]:
    return [
        Rule(
            type=r["type"],
            stage=tuple(r.get("stage") or ("input", "output")),
            action=r.get("action", "log"),
            params=r.get("params") or {},
            fail_mode=r.get("fail_mode"),
        )
        for r in raw
    ]
