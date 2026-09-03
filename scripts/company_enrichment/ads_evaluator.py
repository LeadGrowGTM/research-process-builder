"""Deterministic scorer for the running-ads prompt loop.

Ground truth body (``benchmarks/signals/running-ads-offer-intelligence/ground-truth/<company>.yaml``)::

    company_id: saas-01
    as_of: '2026-08-18'
    channels:
      google: {status: active, evidence_ids: [ev-...]}
      meta:
        status: active
        landing_page: https://example.com/p/enterprise
        observed_offer: enterprise agency reporting plan
        offer_aliases: [enterprise plan]
        call_to_action: Contact us
        evidence_ids: [ev-...]

Components (weights in ``WEIGHTS``):

- ``status`` 0.60: fraction of GT channels whose status the payload matches. A
  channel missing from the payload matches only when GT says ``unknown``.
- ``landing_page`` 0.20: host (without ``www.``) plus path match for GT channels
  that carry a landing page; dropped and renormalized away when none does.
- ``offer`` 0.20: GT-token recall >= 0.5 against ``observed_offer`` or any
  alias, or an exact ``call_to_action`` match, for GT channels that carry copy;
  renormalized away when none does.

Hard failures: ``invalid_output`` (payload violates the ads contract),
``unretained_evidence:<channel>``, ``status_overclaim:<channel>`` (payload says
``active`` where GT is ``inactive``/``unknown``/absent), and
``google_creative_fields`` (copy or a landing page on the Google channel, which
never carries creative text).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from .ads_contracts import AD_CHANNELS, AD_STATUSES, ADS_FIELD, AdsOutput, parse_ads_output, ad_library_channel
from .contracts import CompanyDossier
from .signal_ground_truth import SignalGroundTruthRecord
from .signal_loop import CaseScore


WEIGHTS: Mapping[str, Decimal] = {
    "status": Decimal(".6"), "landing_page": Decimal(".2"), "offer": Decimal(".2"),
}
ADS_EVALUATION_DEPENDENCIES = (ad_library_channel, parse_ads_output)
OFFER_OVERLAP_THRESHOLD = Decimal(".5")
_QUANTUM = Decimal("0.0001")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"a", "an", "and", "for", "in", "of", "on", "the", "to", "with", "your"})
_GT_CHANNEL_KEYS = frozenset({
    "status", "evidence_ids", "landing_page", "observed_offer", "offer_aliases", "call_to_action",
})
_TODO = "TODO_HUMAN"


def _tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(_TOKEN.findall(text.casefold())) - _STOPWORDS


def token_recall(candidate: str | None, reference: str | None) -> Decimal:
    """Fraction of reference tokens (stopwords removed) present in the candidate."""
    reference_tokens = _tokens(reference)
    if not reference_tokens:
        return Decimal("0")
    return Decimal(len(reference_tokens & _tokens(candidate))) / Decimal(len(reference_tokens))


def normalized_landing_page(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    return host, (parsed.path.rstrip("/") or "/")


def _normalized_cta(value: str | None) -> str | None:
    return " ".join(value.split()).casefold() if value else None


def _copy_terms(expected: Mapping[str, Any]) -> tuple[str, ...]:
    terms = [expected.get("observed_offer"), *(expected.get("offer_aliases") or ())]
    return tuple(item for item in terms if isinstance(item, str) and item.strip())


def _has_copy(expected: Mapping[str, Any]) -> bool:
    return bool(_copy_terms(expected)) or bool(expected.get("call_to_action"))


def _reject_todo(value: Any, field: str) -> None:
    if isinstance(value, str) and value.strip() == _TODO:
        raise ValueError(f"{field} still holds the {_TODO} placeholder")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_todo(item, f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_todo(item, f"{field}[{index}]")


def validate_ads_record(record: SignalGroundTruthRecord, dossier: CompanyDossier) -> None:
    """Record validator handed to ``dataset_loader``: strict GT body shape."""
    body = record.body
    if set(body) != {"channels", "as_of"}:
        raise ValueError(f"ground truth {record.company_id} keys must be company_id, as_of, channels")
    if not isinstance(body["as_of"], (str, date)) or not str(body["as_of"]).strip():
        raise ValueError(f"ground truth {record.company_id} as_of must be a date")
    channels = body["channels"]
    if not isinstance(channels, Mapping) or not channels:
        raise ValueError(f"ground truth {record.company_id} channels must be a non-empty mapping")
    for name, expected in channels.items():
        label = f"ground truth {record.company_id} channel {name}"
        if name not in AD_CHANNELS:
            raise ValueError(f"{label} is not a supported ad channel")
        if not isinstance(expected, Mapping) or set(expected) - _GT_CHANNEL_KEYS:
            raise ValueError(f"{label} has unexpected keys")
        status = expected.get("status")
        if status not in AD_STATUSES:
            raise ValueError(f"{label} status must be one of {list(AD_STATUSES)}")
        if (status != "unknown") != ("evidence_ids" in expected):
            raise ValueError(f"{label} must cite evidence exactly when its status is known")
        landing_page = expected.get("landing_page")
        if landing_page is not None:
            parsed = urlparse(str(landing_page))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{label} landing_page must be an absolute HTTP(S) URL")
        aliases = expected.get("offer_aliases")
        if aliases is not None and (
            not isinstance(aliases, (list, tuple))
            or any(not isinstance(item, str) or not item.strip() for item in aliases)
        ):
            raise ValueError(f"{label} offer_aliases must be a list of text")
        if name == "google" and (_has_copy(expected) or landing_page is not None):
            raise ValueError(f"{label} must not carry copy or a landing page")
        _reject_todo(expected, label)


def _status_component(output: AdsOutput, channels: Mapping[str, Mapping[str, Any]]) -> Decimal:
    matched = 0
    for name, expected in channels.items():
        actual = output.channel(name)
        if actual is None:
            matched += expected["status"] == "unknown"
        else:
            matched += actual.status == expected["status"]
    return Decimal(matched) / Decimal(len(channels))


def _landing_component(
    output: AdsOutput, channels: Mapping[str, Mapping[str, Any]],
) -> Decimal | None:
    applicable = {name: item for name, item in channels.items() if item.get("landing_page")}
    if not applicable:
        return None
    matched = 0
    for name, expected in applicable.items():
        actual = output.channel(name)
        if actual is not None and actual.landing_page is not None:
            matched += (normalized_landing_page(actual.landing_page)
                        == normalized_landing_page(str(expected["landing_page"])))
    return Decimal(matched) / Decimal(len(applicable))


def _offer_component(
    output: AdsOutput, channels: Mapping[str, Mapping[str, Any]],
) -> Decimal | None:
    applicable = {name: item for name, item in channels.items() if _has_copy(item)}
    if not applicable:
        return None
    matched = 0
    for name, expected in applicable.items():
        actual = output.channel(name)
        if actual is None:
            continue
        offer_hit = any(
            token_recall(actual.offer, term) >= OFFER_OVERLAP_THRESHOLD
            for term in _copy_terms(expected)
        )
        expected_cta = _normalized_cta(expected.get("call_to_action"))
        cta_hit = expected_cta is not None and _normalized_cta(actual.call_to_action) == expected_cta
        matched += offer_hit or cta_hit
    return Decimal(matched) / Decimal(len(applicable))


def _hard_failures(
    output: AdsOutput, channels: Mapping[str, Mapping[str, Any]],
    channel_by_evidence: Mapping[str, str | None] | None = None,
) -> tuple[str, ...]:
    failures: list[str] = []
    for item in output.channels:
        if channel_by_evidence is not None and any(
            channel_by_evidence.get(evidence_id) != item.channel
            for evidence_id in item.evidence_ids
        ):
            failures.append(f"cross_channel_citation:{item.channel}")
        expected_status = channels.get(item.channel, {}).get("status", "unknown")
        if item.status == "active" and expected_status in {"inactive", "unknown"}:
            failures.append(f"status_overclaim:{item.channel}")
        if item.channel == "google" and (item.has_copy or item.landing_page is not None):
            failures.append("google_creative_fields")
    return tuple(failures)


def _weighted(components: Mapping[str, Decimal]) -> Decimal:
    total = sum((WEIGHTS[key] for key in components), Decimal("0"))
    if not total:
        return Decimal("0")
    weighted = sum((WEIGHTS[key] * value for key, value in components.items()), Decimal("0"))
    return (weighted / total).quantize(_QUANTUM)


def score_ads(
    payload: Mapping[str, Any], record: SignalGroundTruthRecord, dossier: CompanyDossier,
) -> CaseScore:
    retained = {item.evidence_id for item in dossier.evidence}
    zero = {key: Decimal("0") for key in WEIGHTS}
    try:
        output = parse_ads_output(payload, retained | _cited(payload))
    except ValueError:
        return CaseScore(record.company_id, zero, Decimal("0"), ("invalid_output",))
    unretained = tuple(
        f"unretained_evidence:{item.channel}" for item in output.channels
        if not set(item.evidence_ids) <= retained
    )
    channels = record.body["channels"]
    components: dict[str, Decimal] = {"status": _status_component(output, channels)}
    for key, value in (
        ("landing_page", _landing_component(output, channels)),
        ("offer", _offer_component(output, channels)),
    ):
        if value is not None:
            components[key] = value
    channel_by_evidence = {
        item.evidence_id: ad_library_channel(item.url) for item in dossier.evidence
    }
    return CaseScore(
        record.company_id, components, _weighted(components),
        (*unretained, *_hard_failures(output, channels, channel_by_evidence)),
    )


def _cited(payload: Any) -> set[str]:
    """Evidence IDs the payload cites, so parsing can proceed and citation
    misses surface as their own hard failure rather than ``invalid_output``."""
    found: set[str] = set()
    try:
        for item in payload.get(ADS_FIELD, {}).get("channels", ()):
            ids = item.get("evidence_ids", ()) if isinstance(item, Mapping) else ()
            found.update(ref for ref in ids if isinstance(ref, str))
    except AttributeError:
        return found
    return found
