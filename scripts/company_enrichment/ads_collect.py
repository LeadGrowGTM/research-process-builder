"""Collect running-ads Evidence for one benchmark company into a signal dossier.

``collect_ads`` is the ``SignalSpec.collect`` stage for
``running-ads-offer-intelligence``: it fans out to the free Google Ads
Transparency and Meta Ad Library providers, turns each ``SourceObservation``
into content-addressed Evidence (the same ``ev-<sha256[:16]>`` derivation the
base dossiers use), and records one deterministic ``ads`` assertion whose value
lists every channel. A channel whose provider failed appears with status
``unknown``, no evidence, and the normalized failure reason; ``ads`` lands in
the dossier ``unknowns`` only when every channel is unknown.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.parse import urlparse

from .adapters.ads_channels import (
    AdChannelOutcome, ad_finding_to_source_records, ad_finding_value, collect_ad_findings,
)
from .adapters.google_ads import build_google_ads_client
from .adapters.meta_ads import build_meta_ads_client
from .ads_contracts import ADS_FIELD
from .contracts import CompanyDossier, EvidenceRef, SellerContext, canonical_json
from .corpus import Corpus
from .evidence import SourceRecord
from .executors import Finding, execute_p0
from .providers import AdStatus, AdsProvider, AdsRequest
from .signal_evidence import signal_dossier
from .signal_loop import CollectRequest


ENRICHMENT_ID = "running-ads-offer-intelligence"
CORPUS_PATH = Path("benchmarks/companies.yaml")

# The P0 executor requires a seller context; the benchmark runs seller-neutral,
# so this mirrors the fixed context used by the corpus builder in cli.py.
_SELLER_CONTEXT = SellerContext(
    "B2B teams", ("business leader",), ("company research",),
    "Research workflow", "30 days", "validated company context",
    ("cited Evidence",), "explicit unknowns", ("consumer targeting",),
    "invest in evidence-backed research",
)


def evidence_ref(record: SourceRecord) -> EvidenceRef:
    """Content-address a SourceRecord exactly like ``EvidenceStore.put`` does."""
    content_hash = sha256(record.content.encode("utf-8")).hexdigest()
    return EvidenceRef(
        f"ev-{content_hash[:16]}", record.url, record.retrieved_at, content_hash, record.excerpt,
    )


def _identity(base: CompanyDossier) -> str | None:
    assertion = next((item for item in base.assertions if item.field == "identity"), None)
    if assertion is None or not isinstance(assertion.value, str) or not assertion.value.strip():
        return None
    return assertion.value.strip()


def _fixture(repo_root: Path, company_id: str):
    path = Path(repo_root) / CORPUS_PATH
    if not path.is_file():
        return None
    return next((item for item in Corpus.load(path).fixtures if item.id == company_id), None)


def ads_request(request: CollectRequest) -> AdsRequest:
    """Company name from the base ``identity`` assertion; website from the corpus
    fixture domain when present, else the first base Evidence origin."""
    fixture = _fixture(request.repo_root, request.company_id)
    name = _identity(request.base) or (fixture.company_name if fixture else None)
    if name is None:
        raise ValueError(f"{request.company_id} has no identity assertion or corpus fixture")
    if fixture is not None:
        return AdsRequest(name, f"https://{fixture.domain}")
    if not request.base.evidence:
        raise ValueError(f"{request.company_id} has no base Evidence to derive a website")
    parsed = urlparse(request.base.evidence[0].url)
    return AdsRequest(name, f"{parsed.scheme}://{parsed.netloc}")


def _default_providers() -> dict[str, AdsProvider]:
    return {"google": build_google_ads_client(), "meta": build_meta_ads_client()}


def _channel_value(outcome: AdChannelOutcome, refs: tuple[EvidenceRef, ...]) -> dict[str, Any]:
    failure = outcome.failure
    return {
        **ad_finding_value(outcome.finding),
        "evidence_ids": [item.evidence_id for item in refs],
        "failure": None if failure is None else {
            "kind": failure.kind.value, "message": failure.message,
        },
    }


def collect_ads(
    request: CollectRequest, *, providers: Mapping[str, AdsProvider] | None = None,
    now: datetime | None = None,
) -> CompanyDossier:
    """Build the running-ads signal dossier for one company. Provider failures
    never raise; they become unknown channels with a recorded reason."""
    retrieved_at = now or datetime.now(timezone.utc)
    if retrieved_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if providers is None:
        providers = _default_providers()
    outcomes = collect_ad_findings(ads_request(request), providers)

    refs: list[EvidenceRef] = []
    channels: list[dict[str, Any]] = []
    for outcome in outcomes:
        channel_refs = tuple(
            evidence_ref(record) for record in
            ad_finding_to_source_records(outcome.finding, retrieved_at=retrieved_at)
        )
        refs.extend(channel_refs)
        channels.append(_channel_value(outcome, channel_refs))
        if outcome.failure is not None:
            print(canonical_json({
                "channel": outcome.finding.channel, "company_id": request.company_id,
                "failure": outcome.failure.kind.value, "message": outcome.failure.message,
            }), file=sys.stderr)

    all_unknown = all(item.finding.status is AdStatus.UNKNOWN for item in outcomes)
    if all_unknown or not refs:
        return signal_dossier(request.company_id, request.base, (), (), (ADS_FIELD,))

    unique_refs = tuple(dict.fromkeys(refs))
    output = execute_p0(
        ENRICHMENT_ID, unique_refs, seller_context=_SELLER_CONTEXT,
        findings=(Finding(ADS_FIELD, {"channels": channels}),),
    )
    dossier = signal_dossier(request.company_id, request.base, unique_refs, output.assertions)
    # The base marks ``ads`` unknown because base collection never covered it;
    # the signal dossier now asserts it, so the marker no longer applies.
    return replace(dossier, unknowns=tuple(item for item in dossier.unknowns if item != ADS_FIELD))
