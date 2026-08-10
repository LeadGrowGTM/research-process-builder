"""Contract tests for the repository-scoped Codex MCP configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".codex" / "config.toml"
PARALLEL_DOC = ROOT / "docs" / "providers" / "parallel-search-mcp.md"
GTM_DOC = ROOT / "docs" / "providers" / "gtm-mcp-read-interface.md"


def _load_config() -> dict[str, object]:
    with CONFIG_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _literal_credential_fields(value: object, path: tuple[str, ...] = ()) -> list[str]:
    if not isinstance(value, Mapping):
        return []

    findings: list[str] = []
    for key, nested_value in value.items():
        nested_path = (*path, str(key))
        normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        is_environment_reference = normalized_key.endswith(
            ("_env", "_env_var", "_environment_variable")
        )
        has_credential_marker = any(
            marker in normalized_key for marker in ("token", "key", "secret", "password")
        )
        if has_credential_marker and not is_environment_reference:
            if isinstance(nested_value, str) and nested_value:
                findings.append(".".join(nested_path))
        findings.extend(_literal_credential_fields(nested_value, nested_path))
    return findings


def _simulate_missing_oauth(config: Mapping[str, object]) -> str:
    """Model the local operator guidance path without opening an MCP connection."""
    servers = config["mcp_servers"]
    parallel = servers["parallel-search"]
    if parallel["auth"] == "oauth":
        return "Authentication is required; run: codex mcp login parallel-search"
    raise AssertionError("the configured server must require OAuth")


def test_project_config_declares_the_restricted_parallel_oauth_server() -> None:
    """A wrong endpoint, host schema, or broadened tool set must fail this contract."""
    config = _load_config()

    parallel = config["mcp_servers"]["parallel-search"]

    assert parallel == {
        "url": "https://search.parallel.ai/mcp-oauth",
        "auth": "oauth",
        "enabled_tools": ["web_search", "web_fetch"],
        "default_tools_approval_mode": "prompt",
    }


def test_config_contains_no_inline_credential_values() -> None:
    """Adding any literal credential-bearing field to tracked config must fail."""
    assert _literal_credential_fields(_load_config()) == []


def test_missing_oauth_is_locally_actionable_without_a_remote_connection() -> None:
    """A missing OAuth session must direct the operator to the local login command."""
    assert _simulate_missing_oauth(_load_config()) == (
        "Authentication is required; run: codex mcp login parallel-search"
    )


def test_provider_docs_record_evidence_and_read_only_boundaries() -> None:
    """Removing verified provenance or broadening GTM beyond reads must fail."""
    parallel = PARALLEL_DOC.read_text(encoding="utf-8")
    gtm = GTM_DOC.read_text(encoding="utf-8")

    assert "https://developers.openai.com/codex/mcp" in parallel
    assert "https://docs.parallel.ai/integrations/mcp/search-mcp" in parallel
    assert "2026-08-10" in parallel
    assert "codex mcp login parallel-search" in parallel
    assert "Do not authenticate, connect, or query" in " ".join(parallel.split())
    assert "UNVERIFIED" in gtm
    assert "No Clay or full GTM provider is implemented" in gtm
    assert "lists knowledge-category status and counts" not in gtm


@pytest.mark.parametrize(
    ("fixture", "expected_path"),
    [
        ({"token": "value"}, "token"),
        ({"client_secret": "value"}, "client_secret"),
        ({"parallel_key": "value"}, "parallel_key"),
        ({"api-key-backup": "value"}, "api-key-backup"),
        ({"MiXeD__PaSsWoRd": "value"}, "MiXeD__PaSsWoRd"),
    ],
)
def test_literal_credential_key_variants_are_rejected(
    fixture: dict[str, str], expected_path: str
) -> None:
    """Any literal under a representative credential-bearing key must be found."""
    assert _literal_credential_fields(fixture) == [expected_path]


def test_safe_config_values_and_environment_references_are_not_rejected() -> None:
    """Auth modes, URLs, tool names, and env-reference fields are not credentials."""
    fixture = {
        "auth": "oauth",
        "url": "https://search.parallel.ai/mcp-oauth",
        "enabled_tools": ["web_search", "web_fetch"],
        "parallel_token_env_var": "PARALLEL_SEARCH_AUTH",
    }

    assert _literal_credential_fields(fixture) == []
