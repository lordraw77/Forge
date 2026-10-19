"""Scores a detector on the injection dataset (US-1.21.1): block rate on attacks and false-positive
rate on benign texts, overall and by language and category.

    forge injection run --detector heuristic --min-block-rate 0.6 --max-fp-rate 0.05
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.guardrails.injection.detectors import Detector

DATA_DIR = Path(__file__).parent / "data"


@dataclass
class Dataset:
    version: str
    attacks: list[dict[str, str]]
    benign: list[dict[str, str]]
    manifest: dict[str, Any]
    holdout_attacks: list[dict[str, str]] = field(default_factory=list)
    holdout_benign: list[dict[str, str]] = field(default_factory=list)

    def subset(self, which: str) -> Dataset:
        """`main` (generated), `holdout` (hand-written) or `all`."""
        if which == "holdout":
            return Dataset(self.version, self.holdout_attacks, self.holdout_benign, self.manifest)
        if which == "all":
            return Dataset(
                self.version,
                self.attacks + self.holdout_attacks,
                self.benign + self.holdout_benign,
                self.manifest,
            )
        return Dataset(self.version, self.attacks, self.benign, self.manifest)


@dataclass
class Tally:
    total: int = 0
    blocked: int = 0

    @property
    def rate(self) -> float:
        return self.blocked / self.total if self.total else 0.0


@dataclass
class Report:
    detector: str
    dataset_version: str
    block_rate: float
    false_positive_rate: float
    attacks: int
    benign: int
    by_language: dict[str, dict[str, float]] = field(default_factory=dict)
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)
    missed: list[dict[str, str]] = field(default_factory=list)
    false_positives: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "dataset_version": self.dataset_version,
            "attacks": self.attacks,
            "benign": self.benign,
            "block_rate": round(self.block_rate, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "by_language": self.by_language,
            "by_category": self.by_category,
        }


def load_dataset(version: str = "1", base: Path | None = None) -> Dataset:
    """Reads the files and checks them against the manifest (hash and counts)."""
    folder = (base or DATA_DIR) / f"v{version}"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    rows: dict[str, list[dict[str, str]]] = {}
    for name in ("attacks.jsonl", "benign.jsonl", "holdout_attacks.jsonl", "holdout_benign.jsonl"):
        raw = (folder / name).read_bytes()
        expected = manifest["files"][name]
        if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError(f"{name} does not match its manifest hash")
        rows[name] = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
        if len(rows[name]) != expected["records"]:
            raise ValueError(
                f"{name} has {len(rows[name])} records, manifest says {expected['records']}"
            )
    return Dataset(
        manifest["version"],
        rows["attacks.jsonl"],
        rows["benign.jsonl"],
        manifest,
        rows["holdout_attacks.jsonl"],
        rows["holdout_benign.jsonl"],
    )


def score(detector: Detector, name: str, data: Dataset, *, samples: int = 5) -> Report:
    by_lang: dict[str, Tally] = defaultdict(Tally)
    by_cat: dict[str, Tally] = defaultdict(Tally)
    missed: list[dict[str, str]] = []
    false_pos: list[dict[str, str]] = []
    atk, ben = Tally(), Tally()
    for row in data.attacks:
        blocked = detector(row["text"])
        for tally in (atk, by_lang[f"attack:{row['lang']}"], by_cat[f"attack:{row['category']}"]):
            tally.total += 1
            tally.blocked += blocked
        if not blocked:
            missed.append(row)
    for row in data.benign:
        blocked = detector(row["text"])
        for tally in (ben, by_lang[f"benign:{row['lang']}"], by_cat[f"benign:{row['category']}"]):
            tally.total += 1
            tally.blocked += blocked
        if blocked:
            false_pos.append(row)

    def table(tallies: dict[str, Tally]) -> dict[str, dict[str, float]]:
        return {
            key: {"total": t.total, "blocked": t.blocked, "rate": round(t.rate, 4)}
            for key, t in sorted(tallies.items())
        }

    return Report(
        detector=name,
        dataset_version=data.version,
        block_rate=atk.rate,
        false_positive_rate=ben.rate,
        attacks=atk.total,
        benign=ben.total,
        by_language=table(by_lang),
        by_category=table(by_cat),
        missed=missed[: max(samples, 0)] if samples >= 0 else missed,
        false_positives=false_pos[: max(samples, 0)] if samples >= 0 else false_pos,
    )


def format_report(report: Report) -> str:
    lines = [
        f"Injection dataset v{report.dataset_version}, detector '{report.detector}'",
        f"  attacks blocked:        {report.block_rate:6.1%}  ({report.attacks} attacks)",
        f"  benign blocked (FP):    {report.false_positive_rate:6.1%}  ({report.benign} benign)",
        "",
        "  by language",
    ]
    for key, row in report.by_language.items():
        lines.append(f"    {key:<24}{row['rate']:7.1%}  ({row['blocked']}/{row['total']})")
    lines.append("  by category")
    for key, row in report.by_category.items():
        lines.append(f"    {key:<24}{row['rate']:7.1%}  ({row['blocked']}/{row['total']})")
    if report.missed:
        lines += ["", "  missed attacks (sample)"]
        lines += [f"    {r['id']} [{r['category']}] {r['text'][:90]!r}" for r in report.missed]
    if report.false_positives:
        lines += ["", "  false positives (sample)"]
        lines += [
            f"    {r['id']} [{r['category']}] {r['text'][:90]!r}" for r in report.false_positives
        ]
    return "\n".join(lines)
