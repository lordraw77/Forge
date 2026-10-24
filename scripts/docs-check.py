#!/usr/bin/env python3
"""Checks the user guide (docs/guide): links and pictures resolve, and the English and Italian
chapters have the same shape (same number of headings, pictures and tables)."""
import re
import sys
from pathlib import Path

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "guide"
LINK = re.compile(r"!?\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
problems: list[str] = []


def shape(text: str) -> tuple[int, int, int, int]:
    heads = len(re.findall(r"^#{1,4} ", text, re.M))
    pics = len(re.findall(r"!\[[^\]]*\]\(", text))
    tables = len(re.findall(r"^\| ?---", text, re.M))
    blocks = len(re.findall(r"^```", text, re.M))
    return heads, pics, tables, blocks


files = sorted(GUIDE.glob("*/*.md")) + [GUIDE / "README.md"]
for f in files:
    text = f.read_text()
    for target in LINK.findall(text):
        if re.match(r"[a-z]+:", target):
            continue
        if not (f.parent / target).resolve().exists():
            problems.append(f"{f.relative_to(GUIDE)}: missing {target}")

en = {p.name.split("-", 1)[0]: p for p in (GUIDE / "en").glob("[0-9][0-9]-*.md")}
it = {p.name.split("-", 1)[0]: p for p in (GUIDE / "it").glob("[0-9][0-9]-*.md")}
for number in sorted(set(en) | set(it)):
    if number not in en or number not in it:
        problems.append(f"chapter {number} exists in one language only")
        continue
    a, b = shape(en[number].read_text()), shape(it[number].read_text())
    if a != b:
        problems.append(f"chapter {number}: (headings, pictures, tables, code blocks) en={a} it={b}")

pics = {p.name for p in (GUIDE / "images" / "en").glob("*.png")}
other = {p.name for p in (GUIDE / "images" / "it").glob("*.png")}
for name in sorted(pics ^ other):
    problems.append(f"picture {name} exists in one language only")

print("\n".join(problems) or "guide ok")
sys.exit(1 if problems else 0)
