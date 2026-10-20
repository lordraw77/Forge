"""Named-entity PII (names, places, organisations) with Presidio and small spaCy models
(E2.11, ADR-013). Optional: the `pii` extra installs Presidio and spaCy; the model files
(`en_core_web_sm`, `it_core_news_sm`, about 13 MB each) live in `FORGE_MODELS_DIR/spacy`, baked
into the embedded image or downloaded on first use in the `-slim` one (through the proxy).

`detect` answers `None` when the capability is missing, so a rule can fail open or closed.
"""

from __future__ import annotations

import io
import os
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from forge.core.logging import get_logger
from forge.guardrails.engine import NER_ENTITIES, Span

log = get_logger(__name__)

MODELS_DIR = Path(os.environ.get("FORGE_MODELS_DIR", "/app/models")) / "spacy"
SPACY_MODELS = {"en": "en_core_web_sm", "it": "it_core_news_sm"}
MODEL_VERSION = "3.8.0"
URL = "https://github.com/explosion/spacy-models/releases/download/{n}-{v}/{n}-{v}-py3-none-any.whl"
RETRY_AFTER_S = 300.0
# spaCy label (English and Italian models) -> Forge entity
_LABELS = {
    "PERSON": "PERSON", "PER": "PERSON",
    "GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION",
    "ORG": "ORGANIZATION",
}  # fmt: skip

_EN = "the and of to in is are was for with that this on at from by my your"
_IT = "il lo la le gli di del della che per con un una è sono nel nella ho mio tuo e"
_STOPWORDS = {"en": frozenset(_EN.split()), "it": frozenset(_IT.split())}
_lock = threading.Lock()
_engine: dict[tuple[str, ...], Any] = {}
_failed_at: float | None = None


def _path(lang: str) -> Path:
    name = SPACY_MODELS[lang]
    return MODELS_DIR / name / f"{name}-{MODEL_VERSION}"


def installed() -> bool:
    try:
        import presidio_analyzer  # noqa: F401
        import spacy  # noqa: F401
    except ImportError:
        return False
    return True


def models_present(languages: tuple[str, ...] = tuple(SPACY_MODELS)) -> bool:
    return all((_path(lang) / "meta.json").exists() for lang in languages)


def fetch(languages: tuple[str, ...] = tuple(SPACY_MODELS)) -> None:
    """Download the model wheels and unpack them (build time or first use); raises on error."""
    for lang in languages:
        if (_path(lang) / "meta.json").exists():
            continue
        name = SPACY_MODELS[lang]
        url = URL.format(n=name, v=MODEL_VERSION)
        if not url.startswith("https://"):
            raise ValueError("model URL must be https")
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - fixed https URL
            data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            wheel.extractall(MODELS_DIR)
        log.info("guardrails.spacy_model", model=name, path=str(_path(lang)))


def _build(languages: tuple[str, ...]) -> Any:
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    conf = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": lang, "model_name": str(_path(lang))} for lang in languages],
    }
    nlp = NlpEngineProvider(nlp_configuration=conf).create_engine()
    registry = RecognizerRegistry(supported_languages=list(languages))
    registry.load_predefined_recognizers(languages=list(languages), nlp_engine=nlp)
    return AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=list(languages))


def _analyzer(languages: tuple[str, ...]) -> Any | None:
    global _failed_at
    if not installed():
        return None
    with _lock:
        if languages in _engine:
            return _engine[languages]
        if _failed_at is not None and time.monotonic() - _failed_at < RETRY_AFTER_S:
            return None
        try:
            if not models_present(languages) and os.environ.get("FORGE_MODELS_MODE") != "embedded":
                fetch(languages)
            if not models_present(languages):
                raise FileNotFoundError("spaCy models are not installed")
            _engine[languages] = _build(languages)
            return _engine[languages]
        except Exception as exc:  # network, missing files, incompatible model
            _failed_at = time.monotonic()
            log.error("guardrails.ner_unavailable", error=str(exc)[:300])
            return None


def available(languages: tuple[str, ...] = tuple(SPACY_MODELS)) -> bool:
    return installed() and (
        models_present(languages) or os.environ.get("FORGE_MODELS_MODE") == "download"
    )


def guess_languages(text: str, languages: tuple[str, ...]) -> tuple[str, ...]:
    """The language(s) to analyse in. A model reading the wrong language invents entities
    (an Italian city tagged as an organisation), so pick by common words; a tie keeps both."""
    if len(languages) < 2:
        return languages
    words = text.lower().split()
    score = {
        lang: sum(w.strip(".,;:!?\"'()") in _STOPWORDS.get(lang, ()) for w in words)
        for lang in languages
    }
    top = max(score.values())
    if top == 0:
        return languages
    return tuple(lang for lang in languages if score[lang] == top)


def detect(text: str, entities: list[str], languages: list[str] | None = None) -> list[Span] | None:
    """Spans of the requested NER entities, or None when Presidio or its models are missing.
    Blocking (CPU): callers on the event loop run it in a thread."""
    langs = tuple(lang for lang in (languages or SPACY_MODELS) if lang in SPACY_MODELS)
    wanted = [e for e in entities if e in NER_ENTITIES]
    if not wanted or not langs:
        return []
    engine = _analyzer(langs)
    if engine is None:
        return None
    spans: list[Span] = []
    for lang in guess_languages(text, langs):
        for hit in engine.analyze(text=text, language=lang, entities=wanted, score_threshold=0.5):
            label = _LABELS.get(hit.entity_type, hit.entity_type)
            if label in wanted:
                spans.append(Span(hit.start, hit.end, label))
    return spans
