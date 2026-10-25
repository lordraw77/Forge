"""The deterministic screen in front of long-term memory (E3.6): what the extraction prompt
forbids is also refused by code, whatever the model answered."""

from __future__ import annotations

import pytest

from forge.agents.memory import screen


@pytest.mark.parametrize(
    "fact",
    [
        "The user prefers answers in Italian",
        "The user works on claims for the Bologna office",
        "L'utente preferisce risposte brevi e in tabella",
        "The user is a team lead for motor claims",
    ],
)
def test_ordinary_facts_pass(fact: str) -> None:
    assert screen(fact) is None


@pytest.mark.parametrize(
    ("fact", "reason"),
    [
        ("The user's email is mario.rossi@example.com", "personal_identifier"),
        ("Il codice fiscale dell'utente è RSSMRA85T10A562S", "personal_identifier"),
        ("The user's IBAN is IT60X0542811101000000123456", "personal_identifier"),
        ("The user has diabetes", "sensitive_category"),
        ("L'utente soffre di depressione", "sensitive_category"),
        ("The user votes for a party", "sensitive_category"),
        ("L'utente è di religione musulmana", "sensitive_category"),
        ("The user has a criminal record", "sensitive_category"),
    ],
)
def test_identifiers_and_special_categories_are_refused(fact: str, reason: str) -> None:
    assert screen(fact) == reason
