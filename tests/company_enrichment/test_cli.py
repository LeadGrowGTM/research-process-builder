import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.company_enrichment.cli import CORPUS_PAID_CAP_USD, ResearchSource, main


class FakeClient:
    def __init__(self, calls, *, fail_id=None):
        self.calls = calls
        self.fail_id = fail_id

    def research(self, fixture):
        self.calls.append(fixture.id)
        if fixture.id == self.fail_id:
            raise PermissionError('authentication required')
        return (
            ResearchSource(f'https://{fixture.domain}', 'first_party', 'official',
                           f'{fixture.company_name} provides B2B software for teams. ' * 10),
            ResearchSource(f'https://linkedin.test/{fixture.id}', 'independent', 'linkedin',
                           f'{fixture.company_name} company profile. ' * 10),
            ResearchSource(f'https://reviews.test/{fixture.id}', 'independent', 'reviews',
                           f'{fixture.company_name} customer reviews. ' * 10),
        )


def _run(tmp_path: Path, args, *, fail_id=None):
    calls = []
    created = []
    output = []

    def factory():
        created.append(True)
        return FakeClient(calls, fail_id=fail_id)

    code = main(
        [*args, '--run-dir', str(tmp_path)],
        client_factory=factory, emit=output.append,
    )
    return code, calls, created, json.loads(output[-1])


def test_dry_run_constructs_no_clients_and_reports_exact_stage(tmp_path: Path) -> None:
    code, calls, created, summary = _run(
        tmp_path, ['--stage', 'saas_shared_core', '--dry-run'],
    )
    assert code == 0
    assert created == calls == []
    assert summary['company_ids'] == ['saas-01', 'saas-04', 'saas-07']
    assert summary['paid_cap_usd'] == '0'


def test_script_bridge_is_directly_executable(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, 'scripts/company_enrichment_cli.py',
         '--stage', 'saas_shared_core', '--run-dir', str(tmp_path), '--dry-run'],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(completed.stdout)['company_ids'] == [
        'saas-01', 'saas-04', 'saas-07',
    ]


def test_live_stage_runs_exact_ids_once_and_resume_skips_all(tmp_path: Path) -> None:
    first = _run(tmp_path, ['--stage', 'saas_shared_core'])
    assert first[1] == ['saas-01', 'saas-04', 'saas-07']
    assert first[3]['dossiers_valid'] == 3
    assert first[3]['duplicate_ids'] == 0
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[1] == []
    assert resumed[3]['resumed'] == 3
    assert resumed[3]['source_repurchases'] == 0
    reports = [
        json.loads(line)
        for line in (tmp_path / 'stage-report.jsonl').read_text().splitlines()
    ]
    assert len(reports) == 2
    assert reports[0]['unknown_reasons']['saas-01']['ads'] == (
        'not established after source saturation and dry-angle research'
    )


def test_paid_execution_requires_explicit_opt_in_and_fixed_corpus_cap(
    tmp_path: Path,
) -> None:
    assert CORPUS_PAID_CAP_USD == '2.00'
    with pytest.raises(SystemExit):
        _run(tmp_path, ['--stage', 'saas_shared_core', '--paid-cap-usd', '2.00'])
    with pytest.raises(SystemExit):
        _run(tmp_path, ['--stage', 'saas_shared_core', '--allow-paid',
                        '--paid-cap-usd', '1.00'])


def test_authentication_gap_is_redacted_in_json_summary(tmp_path: Path) -> None:
    code, _calls, _created, summary = _run(
        tmp_path, ['--stage', 'saas_shared_core'], fail_id='saas-04',
    )
    assert code == 2
    assert summary['authentication_gaps'] == ['saas-04']
    assert 'authentication required' not in json.dumps(summary)


def test_live_run_persists_raw_evidence_and_research_complete_dossiers(
    tmp_path: Path,
) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    assert len(list((tmp_path / 'evidence' / 'objects').glob('*.json'))) == 9
    dossiers = list((tmp_path / 'dossiers').glob('*.yaml'))
    assert len(dossiers) == 3
    assert all(yaml.safe_load(path.read_text())['unknowns'] for path in dossiers)


def test_live_summary_records_source_saturation_and_dry_angles(tmp_path: Path) -> None:
    _code, _calls, _created, summary = _run(
        tmp_path, ['--stage', 'saas_shared_core'],
    )
    assert summary['sources_persisted'] == 9
    assert summary['companies_saturated'] == 3
    assert summary['dry_angles'] == {
        'saas-01': ['ad_transparency', 'funding_transaction'],
        'saas-04': ['ad_transparency', 'public_dollar_pricing'],
        'saas-07': ['ad_transparency', 'audited_financials'],
    }
