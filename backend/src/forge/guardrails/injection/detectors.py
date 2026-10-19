"""Detectors the runner can score (US-1.21.1). A detector answers one question: block this text?

* `none`: blocks nothing. The floor: 0 % blocked, 0 % false positives.
* `heuristic`: normalised-text rules in English and Italian. A baseline shipped in Phase 1 to
  validate the runner and give the Phase 2 guardrail models (E2.11, ADR-013) a number to beat.
  It is not a security control.
* `command:<program and arguments>`: runs a program per text (text on stdin, `block` or `allow`
  on stdout), so any external detector can be scored without code changes.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import shlex
import subprocess
import unicodedata
from collections.abc import Callable

Detector = Callable[[str], bool]

ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
HOMOGLYPHS = str.maketrans({"а": "a", "е": "e", "о": "o", "і": "i", "с": "c", "р": "p"})
LEET = str.maketrans({"4": "a", "3": "e", "1": "i", "0": "o", "5": "s", "7": "t"})
BASE64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")

# Each rule is a regular expression over lower-cased, NFKC-normalised text.
RULES: tuple[str, ...] = (
    # override
    r"\b(ignore|disregard|forget|override|bypass|cancel)\b[^.\n]{0,40}\b(previous|prior|above|earlier|original|your|all|system)\b[^.\n]{0,30}\b(instructions?|rules?|guidelines?|prompt|polic(y|ies)|training)\b",
    r"\b(ignora|ignorare|dimentica|scorda|scavalca|trascura|annulla|aggira)\b[^.\n]{0,40}\b(precedenti|precedente|sopra|originali|tue|tutte|sistema)\b[^.\n]{0,30}\b(istruzioni|istruzione|regole|linee guida|prompt|polic[yi]|sicurezza)\b",
    r"\b(ignore|ignora)\b[^.\n]{0,12}\b(the above|quanto sopra|the system prompt|il prompt di sistema)\b",
    r"\b(non seguire|do not follow|stop following|smetti di seguire)\b[^.\n]{0,30}\b(istruzioni|instructions|guidelines|linee guida)\b",
    r"\b(new|nuova)\s+(instruction|istruzione|system prompt|policy)\s*:",
    r"\b(instructions?|istruzioni)\b[^.\n]{0,25}\b(are cancelled|are outdated|sono annullate|sono obsolete)\b",
    # persona and jailbreak
    r"\b(dan|do anything now|developer mode|modalit[àa] sviluppatore|jailbreak|freegpt)\b",
    r"\b(no|without|senza)\s+(any\s+)?(restrictions?|limits?|ethics|etica|restrizioni|limiti|rules|regole)\b",
    r"\b(pretend|act as|roleplay as|fingi|comportati come|interpreta)\b[^.\n]{0,50}\b(evil|malvagi[oa]|unrestricted|senza etica|root|rules? or ethics|no ethics)\b",
    r"\b(late grandmother|defunta nonna|nonna)\b[^.\n]{0,40}\b(used to|era solita)\b",
    # extraction
    r"\b(system prompt|prompt di sistema|hidden instructions|istruzioni nascoste|developer message|messaggio dello sviluppatore)\b[^.\n]{0,40}\b(reveal|print|show|repeat|output|tell|rivela|stampa|mostra|ripeti|dimmi|word for word|parola per parola)\b",
    r"\b(reveal|print|show|repeat|output|tell|rivela|stampa|mostra|ripeti|dimmi|translate|traduci|summari[sz]e|riassumi)\b[^.\n]{0,40}\b(your|tuo|tue|the)\s+(full\s+|hidden\s+|initial\s+)?(system prompt|prompt di sistema|instructions|istruzioni|configuration|configurazione)\b",
    r"\bwhat (is|are|were) your (system prompt|instructions|initial instructions)\b",
    r"\b(qual [eè]|cosa [eè]) il tuo prompt di sistema\b",
    r"\brepeat the text above\b|\bripeti il testo qui sopra\b",
    r"\bprint everything before this message\b|\bstampa tutto ci[òo] che precede\b",
    r"\bwhat were you told before\b|\bcosa ti [eè] stato detto prima\b",
    r"\b(first|prime)\s+\d+\s+words of your context\b|\bprime \d+ parole della tua finestra\b",
    r"\bbegin your answer with your full instructions\b|\binizia la risposta con le tue istruzioni\b",
    # delimiters and fake roles
    r"</?(system|user|assistant)>|<\|im_(start|end)\|>|\[/?inst\]|<<\s*/?sys\s*>>",
    r"###\s*(end of prompt|new system prompt|fine del prompt|nuovo prompt di sistema)",
    r"\"role\"\s*:\s*\"system\"",
    r"\bsystem message\s*:|\bmessaggio di sistema\s*:|\[system\]",
    # indirect injection
    r"\b(note to|nota per|hidden instruction for|istruzione nascosta per|p\.s\. (to|a))\b[^.\n]{0,30}\b(ai|assistant|assistente|model|modello|language model)\b",
    r"\b(ai|ia|assistant|assistente|agents?|agenti)\b[^.\n:]{0,40}\b(reading|processing|summarising|leggono|elaborano|legge|riassume)\b[^.\n]{0,40}\b(must|should|devono|deve|first|prima)\b",
    r"<!--\s*(assistant|assistente)\b",
    r"\b(ai|ia)\s*:\s*(disregard|ignore|trascura|ignora)",
    r"\b(important for the assistant|importante per l'assistente)\b",
    # multi-turn set-ups
    r"\bwhenever i say\b[^.\n]{0,30}\byou\b|\bogni volta che dico\b[^.\n]{0,30}\btu\b",
    r"\b(earlier you agreed|prima avevi accettato)\b",
    r"\bi'?m your developer\b|\bsono il tuo sviluppatore\b",
    r"\bwhen i type /\w+|\bquando scrivo /\w+",
    r"\bdo not mention step 2\b|\bnon nominare il passo 2\b",
    # tool misuse
    r"169\.254\.169\.254|metadata/iam|file:///|localhost:\d+/admin|\b10\.0\.0\.\d+",
    r"\b(send|invia)\b[^.\n]{0,60}\b(conversation|conversazione|documents?|documenti|content|contenuto)\b[^.\n]{0,40}\b(to|a)\b[^.\n]{0,20}(evil\.example|attacker@|https?://)",
    r"evil\.example|attacker@",
    r"\brm -rf\b",
    r"\b(every|all|ogni|tutti)\b[^.\n]{0,30}\b(restricted|riservat[oi])\b[^.\n]{0,20}\b(documents?|documenti)\b",
    # goals that need no preamble
    r"\b(keylogger|malware|ransomware)\b",
    r"\b(without|senza)\b[^.\n]{0,20}\b(checking permissions|controllare i permessi|safety restrictions|restrizione di sicurezza|restrizioni di sicurezza)\b",
    r"\bbypass the content filter\b|\baggirare il filtro dei contenuti\b",
    r"\b(api keys?|chiavi api|database password|password del database)\b[^.\n]{0,40}\b(access to|a cui hai accesso|configuration|configurazione)\b",
)
COMPILED = tuple(re.compile(rule, re.IGNORECASE) for rule in RULES)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH)
    return text.translate(HOMOGLYPHS).lower()


def _variants(text: str) -> list[str]:
    """The text as written, plus decodings an attacker may rely on."""
    base = normalise(text)
    out = [base]
    if any(ch in text for ch in "\u200b\u200c\u200d\u2060\ufeff"):
        # Invisible characters can sit inside words (removed above) or replace the spaces.
        spaced = unicodedata.normalize("NFKC", text).translate(
            dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), " ")
        )
        out.append(re.sub(r"\s+", " ", spaced.translate(HOMOGLYPHS).lower()))
    out.append(codecs.decode(base, "rot13"))
    out.append(base.translate(LEET))
    squeezed = re.sub(r"(?<=\w) (?=\w)", "", base)  # "i g n o r e" -> "ignore"
    if squeezed != base:
        out.append(re.sub(r"\s+", " ", squeezed))
    for blob in BASE64.findall(text):
        try:
            decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        out.append(normalise(decoded))
    return out


def heuristic(text: str) -> bool:
    return any(rule.search(v) for v in _variants(text) for rule in COMPILED)


def none(_text: str) -> bool:
    return False


def command(spec: str) -> Detector:
    argv = shlex.split(spec)
    if not argv:
        raise ValueError("command detector needs a program")

    def run(text: str) -> bool:
        done = subprocess.run(  # noqa: S603 - the operator chose this command
            argv, input=text, capture_output=True, text=True, timeout=60, check=False
        )
        return done.stdout.strip().lower().startswith("block")

    return run


def load(name: str) -> Detector:
    if name == "none":
        return none
    if name == "heuristic":
        return heuristic
    if name.startswith("command:"):
        return command(name.removeprefix("command:"))
    raise ValueError(f"unknown detector {name!r}; use none, heuristic or command:<program>")
