from __future__ import annotations

import base64
import json
import time

import pytest
from pydantic import ValidationError

from forge.cli import build_parser
from forge.core.ids import uuid7
from forge.core.logging import configure_logging, get_logger
from forge.core.rls import enable_project_rls
from forge.core.settings import Settings


def test_uuid7_version_variant_and_order() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first.version == 7
    assert first.variant == "specified in RFC 4122"
    assert first < second


def test_uuid7_embeds_current_millisecond_timestamp() -> None:
    before = time.time_ns() // 1_000_000
    ts = uuid7().int >> 80
    after = time.time_ns() // 1_000_000
    assert before <= ts <= after


def test_master_key_must_be_32_bytes_base64() -> None:
    good = base64.b64encode(b"k" * 32).decode()
    assert Settings(master_key=good).master_key is not None
    with pytest.raises(ValidationError):
        Settings(master_key=base64.b64encode(b"short").decode())
    with pytest.raises(ValidationError):
        Settings(master_key="not base64!!")


def test_secrets_are_not_printed() -> None:
    settings = Settings(admin_password="hunter2-very-secret")
    assert "hunter2" not in repr(settings)


def test_rls_helper_rejects_unsafe_table_names() -> None:
    with pytest.raises(ValueError, match="invalid table name"):
        enable_project_rls("prompt; DROP TABLE x")


def test_rls_helper_dual_scope_reads_platform_rows() -> None:
    sql = enable_project_rls("model", nullable_project=True)
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "project_id IS NULL OR" in sql
    assert "WITH CHECK (project_id = app_project_id())" in sql


def test_log_redacts_secret_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    get_logger("test").info("login", password="hunter2", user="alice")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["password"] == "***"
    assert line["user"] == "alice"
    assert line["msg"] == "login"


def test_cli_roles() -> None:
    parser = build_parser()
    assert parser.parse_args(["worker", "ingest"]).queue == "ingest"
    assert parser.parse_args(["migrate"]).revision == "head"
    with pytest.raises(SystemExit):
        parser.parse_args(["worker", "unknown"])
