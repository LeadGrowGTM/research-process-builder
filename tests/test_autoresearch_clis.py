"""Subprocess contracts shared by the primary and compatibility CLIs."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLIS = (ROOT / "scripts" / "autoresearch_agent.py", ROOT / "scripts" / "autocontext_runner.py")


def invoke(cli, *args):
    return subprocess.run([sys.executable, str(cli), *map(str, args)], cwd=ROOT,
                          text=True, capture_output=True, timeout=20)


@pytest.mark.parametrize("cli", CLIS)
def test_help_and_invalid_arguments_are_noninteractive(cli):
    help_result = invoke(cli, "--help")
    invalid = invoke(cli, "--stub-run")
    assert help_result.returncode == 0 and "--resume" in help_result.stdout
    assert invalid.returncode != 0 and "--run-dir" in invalid.stderr


@pytest.mark.parametrize("cli", CLIS)
def test_dry_run_constructs_no_clients_or_artifacts(cli, tmp_path):
    result = subprocess.run([sys.executable, str(cli), "--dry-run"], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"mode": "dry_run", "paid_cost_ceiling": 0.0}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cli", CLIS)
def test_dry_run_reports_supplied_run_directory_without_creating_it(cli, tmp_path):
    run_dir = tmp_path / "dry"
    result = invoke(cli, "--dry-run", "--run-dir", run_dir)
    assert result.returncode == 0
    assert json.loads(result.stdout)["run_dir"] == str(run_dir)
    assert not run_dir.exists()


@pytest.mark.parametrize("cli", CLIS)
def test_stub_run_and_resume_are_deterministic_and_idempotent(cli, tmp_path):
    run_dir = tmp_path / cli.stem
    first = invoke(cli, "--stub-run", "--run-dir", run_dir)
    assert first.returncode == 0, first.stderr
    first_rows = (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    resumed = invoke(cli, "--resume", run_dir)
    assert resumed.returncode == 0, resumed.stderr
    assert len(first_rows) == 5
    assert (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines() == first_rows
    assert json.loads(first.stdout)["reason_code"] == "human_review_required"
    assert json.loads(resumed.stdout)["reason_code"] == "human_review_required"

@pytest.mark.parametrize("cli", CLIS)
def test_no_arguments_defaults_to_zero_cost_dry_run_without_artifacts(cli, tmp_path):
    result = subprocess.run([sys.executable, str(cli)], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"mode": "dry_run", "paid_cost_ceiling": 0.0}
    assert list(tmp_path.iterdir()) == []
