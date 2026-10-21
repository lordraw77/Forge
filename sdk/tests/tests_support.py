"""Builders shared by the SDK tests."""

from __future__ import annotations

from typing import Any


def prompt(name: str = "Support", text: str = "Hello {name}", version: int = 3) -> dict[str, Any]:
    return {
        "kind": "prompt",
        "name": name,
        "description": "d",
        "versions": [
            {
                "version": version,
                "model": "default-chat",
                "system_template": "You help.",
                "user_template": text,
                "variables": [],
                "examples": [],
                "model_params": {},
                "note": "n",
                "created_at": "2026-10-21T10:00:00+00:00",
            }
        ],
        "tags": {"prod": version},
    }


def bundle(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "forge.bundle",
        "schema_version": 1,
        "forge_version": "0.1.0",
        "exported_at": "2026-10-21T10:00:00+00:00",
        "source": {"project": "P"},
        "items": list(items),
    }
