"""Offline CLI contracts for the optional Serper adapter."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pattern_tester.py"


def _missing_adapter_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["SHARED_SCRIPTS_PATH"] = str(ROOT / "missing-serper-adapter")
    return environment


def _load_pattern_tester_module():
    spec = importlib.util.spec_from_file_location("pattern_tester_cli_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_help_is_available_without_the_optional_adapter() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        env=_missing_adapter_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


@pytest.mark.parametrize("mode", ["--dry-run", "--report", "--generate-doc"])
def test_run_local_modes_without_loading_the_optional_adapter(tmp_path: Path, monkeypatch, mode: str) -> None:
    module = _load_pattern_tester_module()
    monkeypatch.setattr(module, "load_serper_search", lambda: pytest.fail("adapter loaded"))
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), mode, "--output", str(tmp_path / "local-results.json"), "--category", "absent"],
    )

    assert module.main() == 0


@pytest.mark.parametrize("mode, handler", [("--sources", "generate_source_analysis"), ("--migrate", "migrate_all_domains")])
def test_main_local_only_branches_skip_the_optional_adapter(monkeypatch, mode: str, handler: str) -> None:
    module = _load_pattern_tester_module()
    monkeypatch.setattr(module, "load_serper_search", lambda: pytest.fail("adapter loaded"))
    monkeypatch.setattr(module, handler, lambda: None)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), mode])

    assert module.main() == 0


def test_live_query_without_adapter_has_concise_nonzero_diagnostic(tmp_path: Path) -> None:
    config = {
        "categories": [{"id": "test", "variants": [{"id": "one", "template": "query"}]}],
        "test_companies": [{"company_name": "Example", "domain": "example.test", "category": "test"}],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config_path), "--output", str(tmp_path / "output.json")],
        cwd=ROOT,
        env=_missing_adapter_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Could not import serper_search" in result.stderr
    assert "Traceback" not in result.stderr