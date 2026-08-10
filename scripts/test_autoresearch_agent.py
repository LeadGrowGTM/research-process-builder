"""Compatibility contracts for the redesigned thin autoresearch adapter."""

import json

import pytest

from research_orchestration.cli import main


def test_dry_run_is_zero_cost_and_writes_nothing(tmp_path, capsys):
    run_dir = tmp_path / "dry"
    assert main(["--dry-run", "--run-dir", str(run_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["paid_cost_ceiling"] == 0.0
    assert not run_dir.exists()


def test_stub_run_requires_an_explicit_run_directory():
    with pytest.raises(SystemExit) as error:
        main(["--stub-run"])
    assert error.value.code == 2