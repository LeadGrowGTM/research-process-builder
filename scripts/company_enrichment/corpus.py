from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

from .contracts import CompanyDossier, CompanyFixture


REQUIRED_DOSSIER_FIELDS = (
    'identity', 'description', 'offers', 'icp', 'personas', 'news', 'launches',
    'growth', 'ads', 'hiring', 'competitors', 'technology', 'pricing',
)

_ROOT_FIELDS = {'version', 'as_of', 'status', 'source', 'companies'}
_REQUIRED_FIXTURE_FIELDS = {
    'id', 'company_name', 'domain', 'linkedin_company_url', 'primary_cohort',
    'shared_core', 'difficulty', 'seed_status', 'seed', 'gaps',
}
_OPTIONAL_FIXTURE_FIELDS = {
    'b2b_buyer', 'business_offer', 'selection_reason', 'cohort_evidence_url',
    'secondary_tags', 'expected_ad_channels', 'primary_funding_url',
    'primary_funding_date', 'local_listing_url',
}


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode,
                       deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f'duplicate YAML key: {key}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping,
)


def _date_value(value: object, name: str) -> date | None:
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f'{name} must be an ISO date') from error
    raise ValueError(f'{name} must be an ISO date')


def _absolute_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {'http', 'https'} and bool(parsed.netloc)


@dataclass(frozen=True, slots=True)
class Corpus:
    fixtures: tuple[CompanyFixture, ...]
    version: str = '1.0'
    recorded_as_of: date | None = None
    status: str = 'unknown'
    source: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'fixtures', tuple(self.fixtures))
        object.__setattr__(self, 'source', MappingProxyType(dict(self.source)))

    @classmethod
    def load(cls, path: Path) -> 'Corpus':
        try:
            data = yaml.load(Path(path).read_text(encoding='utf-8'),
                             Loader=_UniqueKeyLoader)
        except yaml.YAMLError as error:
            raise ValueError('invalid corpus YAML') from error
        if not isinstance(data, Mapping):
            raise ValueError('corpus YAML root must be a mapping')
        unexpected_root = set(data) - _ROOT_FIELDS
        missing_root = _ROOT_FIELDS - set(data)
        if unexpected_root or missing_root:
            raise ValueError(
                f'invalid corpus fields; missing={sorted(missing_root)}, '
                f'unexpected={sorted(unexpected_root)}'
            )
        companies = data['companies']
        if not isinstance(companies, list):
            raise ValueError('companies must be a list')
        fixtures = tuple(cls._load_fixture(item) for item in companies)
        return cls(
            fixtures, str(data['version']), _date_value(data['as_of'], 'as_of'),
            str(data['status']), data['source'],
        )

    @staticmethod
    def _load_fixture(item: object) -> CompanyFixture:
        if not isinstance(item, Mapping):
            raise ValueError('each company fixture must be a mapping')
        allowed = _REQUIRED_FIXTURE_FIELDS | _OPTIONAL_FIXTURE_FIELDS
        unexpected = set(item) - allowed
        missing = _REQUIRED_FIXTURE_FIELDS - set(item)
        if unexpected:
            raise ValueError(f'unexpected fixture fields: {sorted(unexpected)}')
        if missing:
            raise ValueError(f'missing fixture fields: {sorted(missing)}')
        values = dict(item)
        values['primary_funding_date'] = _date_value(
            values.get('primary_funding_date'), 'primary_funding_date',
        )
        return CompanyFixture(**values)

    def validate(self, as_of: date) -> None:
        if not isinstance(as_of, date):
            raise ValueError('as_of must be a date')
        ids: set[str] = set()
        domains: set[str] = set()
        for fixture in self.fixtures:
            if fixture.id in ids or fixture.domain.casefold() in domains:
                raise ValueError(f'duplicate fixture identity: {fixture.id}')
            ids.add(fixture.id)
            domains.add(fixture.domain.casefold())
            self._validate_fixture(fixture, as_of)

    @staticmethod
    def _validate_fixture(fixture: CompanyFixture, as_of: date) -> None:
        if fixture.domain != fixture.domain.casefold() or '://' in fixture.domain:
            raise ValueError(f'{fixture.id} must have a canonical domain')
        for name in ('b2b_buyer', 'business_offer', 'selection_reason'):
            value = getattr(fixture, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{fixture.id} requires {name}')
        if not _absolute_url(fixture.cohort_evidence_url):
            raise ValueError(f'{fixture.id} requires a cohort evidence URL')
        if not fixture.secondary_tags:
            raise ValueError(f'{fixture.id} requires secondary tags')
        if not fixture.expected_ad_channels:
            raise ValueError(f'{fixture.id} requires expected ad channels')
        if fixture.difficulty not in {'easy', 'ambiguous', 'hard'}:
            raise ValueError(f'{fixture.id} has invalid difficulty')
        if fixture.primary_cohort == 'recently_funded_b2b':
            if not _absolute_url(fixture.primary_funding_url):
                raise ValueError(f'{fixture.id} requires a primary funding URL')
            funding_date = fixture.primary_funding_date
            if funding_date is None:
                raise ValueError(f'{fixture.id} requires a primary funding date')
            cutoff = _twelve_month_cutoff(as_of)
            if not cutoff <= funding_date <= as_of:
                raise ValueError(f'{fixture.id} funding date must be within 12 months')
        if (fixture.primary_cohort == 'local_b2b_services'
                and not _absolute_url(fixture.local_listing_url)):
            raise ValueError(f'{fixture.id} requires a local listing URL')


def _twelve_month_cutoff(as_of: date) -> date:
    try:
        return as_of.replace(year=as_of.year - 1)
    except ValueError:
        return as_of.replace(year=as_of.year - 1, day=28)


def validate_research_complete(
    fixture: CompanyFixture, dossier: CompanyDossier,
) -> None:
    if dossier.company_id != fixture.id:
        raise ValueError('dossier company_id does not match fixture')
    cited = {assertion.field for assertion in dossier.assertions
             if assertion.evidence_ids}
    covered = cited | set(dossier.unknowns)
    missing = [field for field in REQUIRED_DOSSIER_FIELDS if field not in covered]
    if missing:
        names = ', '.join(missing)
        raise ValueError(f'missing required dossier fields: {names}')
