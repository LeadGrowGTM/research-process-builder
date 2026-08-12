import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.company_enrichment.cli import (
    CORPUS_PAID_CAP_USD, ResearchSource, SearchOutcome,
    _stage_fixtures, _validate_source_pack, main,
)


QUALIFICATION_TEXT = {
    'saas-01': 'AgencyAnalytics provides automated reports and custom dashboards for marketing agencies.',
    'saas-04': 'aPriori provides product cost, manufacturability, and carbon footprint software for manufacturers and product designers.',
    'saas-07': 'Betterworks provides performance management and talent intelligence for HR and business leaders.',
}


class FakeSourceClient:
    def __init__(self, calls, *, fail_id=None):
        self.calls = calls
        self.fail_id = fail_id

    def fetch(self, fixture, url):
        self.calls.append((fixture.id, url))
        if fixture.id == self.fail_id:
            raise PermissionError('secret token must never be journaled')
        base = QUALIFICATION_TEXT[fixture.id]
        return (
            base + f' Independent profile for {fixture.company_name} at {url}. '
        ) * 8


class FakeSearchClient:
    def __init__(self, calls, outcomes=None):
        self.calls = calls
        self.outcomes = list(outcomes or ())

    def search(self, fixture, query):
        self.calls.append((fixture.id, query))
        return self.outcomes.pop(0) if self.outcomes else SearchOutcome(False)


def _run(tmp_path: Path, args, *, fail_id=None, search_outcomes=None):
    source_calls = []
    search_calls = []
    created = []
    output = []
    def source_factory():
        created.append('source')
        return FakeSourceClient(source_calls, fail_id=fail_id)
    def search_factory():
        created.append('search')
        return FakeSearchClient(search_calls, search_outcomes)
    code = main(
        [*args, '--run-dir', str(tmp_path)],
        source_client_factory=source_factory,
        search_client_factory=search_factory,
        emit=output.append,
    )
    return code, source_calls, search_calls, created, json.loads(output[-1])


def test_dry_run_constructs_no_clients_and_reports_exact_stage(tmp_path: Path) -> None:
    code, source_calls, search_calls, created, summary = _run(
        tmp_path, ['--stage', 'saas_shared_core', '--dry-run'],
    )
    assert code == 0
    assert created == source_calls == search_calls == []
    assert summary['company_ids'] == ['saas-01', 'saas-04', 'saas-07']
    assert summary['paid_cap_usd'] == '0'


def test_script_bridge_is_directly_executable(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, 'scripts/company_enrichment_cli.py',
         '--stage', 'saas_shared_core', '--run-dir', str(tmp_path), '--dry-run'],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(completed.stdout)['company_ids'] == ['saas-01', 'saas-04', 'saas-07']


def test_live_composes_outer_orchestrator_and_task2_runner(tmp_path: Path) -> None:
    code, source_calls, search_calls, _created, summary = _run(
        tmp_path, ['--stage', 'saas_shared_core'],
    )
    assert code == 0
    assert len(source_calls) == 9
    assert len(search_calls) == 6
    assert summary['dossiers_valid'] == 3
    assert summary['duplicate_ids'] == 0
    assert (tmp_path / 'outer' / 'run.json').exists()
    outcomes = [json.loads(line) for line in (tmp_path / 'outcomes.jsonl').read_text().splitlines()]
    assert len(outcomes) == 24
    assert len({item['enrichment_id'] for item in outcomes}) == 8
    assert all(item['status'] == 'complete' for item in outcomes)
    assert all(item['output']['discovery']['selection_outcome'] == 'selected' for item in outcomes)
    assert all(len(item['output']['route']['provider_ids']) == 3 for item in outcomes)


def test_live_resume_rehydrates_and_validates_without_repurchase(tmp_path: Path) -> None:
    first = _run(tmp_path, ['--stage', 'saas_shared_core'])
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[1] == []
    assert resumed[2] == []
    assert resumed[4]['resumed'] == 3
    assert resumed[4]['source_repurchases'] == 0
    assert resumed[4]['dry_angles'] == first[4]['dry_angles']


def test_resume_does_not_skip_truncated_dossier(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    (tmp_path / 'dossiers' / 'saas-01.yaml').write_text('assertions: [', encoding='utf-8')
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[0] == 2
    assert resumed[4]['resumed'] == 2
    assert resumed[4]['invalid_artifacts'] == ['saas-01']


def test_resume_rejects_orphaned_evidence_object(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    orphan = tmp_path / 'evidence' / 'objects' / (('a' * 64) + '.json')
    orphan.write_text('{"content":"orphan"}', encoding='utf-8')
    resumed = _run(tmp_path, ['--stage', 'saas_shared_core', '--resume'])
    assert resumed[0] == 2
    assert resumed[4]['resumed'] == 0
    assert resumed[4]['invalid_artifacts'] == ['orphaned_evidence_object']


def test_source_gate_requires_distinct_independent_domains_and_entity_relevance() -> None:
    fixture = _stage_fixtures('saas_shared_core')[0]
    official = ResearchSource('https://agencyanalytics.com/about', 'first_party', 'official', QUALIFICATION_TEXT['saas-01'] * 5)
    duplicate_domain = (
        official,
        ResearchSource('https://ca.linkedin.com/a', 'independent', 'linkedin', 'AgencyAnalytics profile ' * 20),
        ResearchSource('https://www.linkedin.com/b', 'independent', 'linkedin-2', 'AgencyAnalytics reviews ' * 20),
    )
    with pytest.raises(ValueError, match='distinct independent domains'):
        _validate_source_pack(fixture, duplicate_domain)
    unrelated = (
        official,
        ResearchSource('https://linkedin.com/a', 'independent', 'linkedin', 'AgencyAnalytics profile ' * 20),
        ResearchSource('https://sourceforge.net/a', 'independent', 'sourceforge', 'Unrelated accounting product ' * 20),
    )
    with pytest.raises(ValueError, match='entity relevance'):
        _validate_source_pack(fixture, unrelated)


def test_qualification_is_extracted_from_retained_sources_not_seed(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    qualification = json.loads((tmp_path / 'qualifications' / 'saas-01.json').read_text())
    assert qualification['b2b_buyer'] == 'marketing agencies'
    assert qualification['business_offer'] == 'automated reports and custom dashboards'
    assert qualification['evidence_id'].startswith('ev-')


def test_dry_angles_are_executed_distinct_outcomes_and_journaled(tmp_path: Path) -> None:
    outcomes = [SearchOutcome(True), SearchOutcome(False), SearchOutcome(False)]
    result = _run(tmp_path, ['--stage', 'saas_shared_core'], search_outcomes=outcomes)
    assert len(result[2]) == 7
    ledger = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    dry = [item for item in ledger if item['kind'] == 'search']
    assert len(dry) == 7
    assert sum(item['status'] == 'no_material_fact' for item in dry) == 6
    assert all('query' in item and 'failure' not in item for item in dry)


def test_call_ledger_redacts_failures_and_records_urls(tmp_path: Path) -> None:
    result = _run(tmp_path, ['--stage', 'saas_shared_core'], fail_id='saas-04')
    assert result[0] == 2
    text = (tmp_path / 'calls.jsonl').read_text()
    assert 'secret token' not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert all(set(item) <= {'company_id', 'failure', 'kind', 'status', 'url', 'query'} for item in rows)
    assert any(item.get('failure') == 'authentication_required' for item in rows)


def test_paid_execution_requires_explicit_opt_in_and_fixed_corpus_cap(tmp_path: Path) -> None:
    assert CORPUS_PAID_CAP_USD == '2.00'
    with pytest.raises(SystemExit):
        _run(tmp_path, ['--stage', 'saas_shared_core', '--paid-cap-usd', '2.00'])
    with pytest.raises(SystemExit):
        _run(tmp_path, ['--stage', 'saas_shared_core', '--allow-paid', '--paid-cap-usd', '1.00'])


def test_live_run_persists_raw_evidence_and_research_complete_dossiers(tmp_path: Path) -> None:
    _run(tmp_path, ['--stage', 'saas_shared_core'])
    assert len(list((tmp_path / 'evidence' / 'objects').glob('*.json'))) == 9
    dossiers = list((tmp_path / 'dossiers').glob('*.yaml'))
    assert len(dossiers) == 3
    assert all(yaml.safe_load(path.read_text())['unknowns'] for path in dossiers)
