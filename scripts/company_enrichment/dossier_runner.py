from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Sequence

import yaml

from .contracts import (
    CompanyDossier, CompanyFixture, EnrichmentResult, EvidenceRef,
    FieldAssertion, ResultStatus, canonical_json,
)
from .corpus import validate_research_complete


class DossierBuilder:
    def __init__(
        self,
        *,
        load_results: Callable[[CompanyFixture, str], Sequence[EnrichmentResult]],
        output_dir: Path,
        as_of,
    ) -> None:
        self._load_results = load_results
        self._output_dir = Path(output_dir)
        self._as_of = as_of

    def build(self, fixture: CompanyFixture, scope: str) -> CompanyDossier:
        results = tuple(self._load_results(fixture, scope))
        assertions: list[FieldAssertion] = []
        evidence_by_id: dict[str, EvidenceRef] = {}
        unknowns: list[str] = []
        for result in results:
            if result.company_id != fixture.id:
                raise ValueError('enrichment result belongs to another fixture')
            if result.status is ResultStatus.FAILED:
                raise ValueError(f'failed enrichment cannot enter dossier: {result.enrichment_id}')
            if (
                result.status is not ResultStatus.COMPLETE
                or result.output.get('saturated') is not True
            ):
                raise ValueError(
                    'dossier inputs must be complete and saturated: '
                    f'{result.enrichment_id}'
                )
            output = result.output
            result_assertions = tuple(output.get('assertions', ()))
            result_evidence = tuple(output.get('evidence', ()))
            if not all(isinstance(item, FieldAssertion) for item in result_assertions):
                raise ValueError('result assertions are not typed')
            if not all(isinstance(item, EvidenceRef) for item in result_evidence):
                raise ValueError('result evidence is not typed')
            assertions.extend(result_assertions)
            for item in result_evidence:
                prior = evidence_by_id.get(item.evidence_id)
                if prior is not None and prior != item:
                    raise ValueError('evidence ID collision across enrichment results')
                evidence_by_id[item.evidence_id] = item
            for field in output.get('unknowns', ()):
                if field not in unknowns:
                    unknowns.append(field)
        dossier = CompanyDossier(
            fixture.id, '1.0', tuple(assertions), tuple(evidence_by_id.values()),
            tuple(unknowns),
        )
        validate_research_complete(fixture, dossier, as_of=self._as_of)
        self._persist(dossier)
        return dossier

    def _persist(self, dossier: CompanyDossier) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        destination = self._output_dir / f'{dossier.company_id}.yaml'
        payload = json.loads(canonical_json(dossier))
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self._output_dir,
                prefix=f'.{dossier.company_id}.', suffix='.tmp', delete=False,
            ) as stream:
                temporary_name = stream.name
                yaml.safe_dump(payload, stream, sort_keys=True, allow_unicode=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)
