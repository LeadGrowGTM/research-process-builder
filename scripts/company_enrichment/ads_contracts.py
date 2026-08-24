"""Immutable, evidence-closed running-ads output contract.

The model returns ``{"ads": {"channels": [...]}, "unknowns": [...]}`` for the
``running-ads-offer-intelligence`` enrichment. ``parse_ads_output`` turns that
payload into typed values and rejects anything the prompt forbids: unknown
channels or statuses, non-URL landing pages, and citations outside the retained
Evidence. ``ads_output_contract`` renders the matching strict JSON schema with
``evidence_ids`` enum-restricted to the dossier's Evidence IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .contracts import CompanyDossier


ADS_FIELD = "ads"
AD_CHANNELS = ("google", "meta")
AD_STATUSES = ("active", "inactive", "unknown")
COPY_FIELDS = ("angle", "offer", "call_to_action")


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be null or non-empty text")
    return " ".join(value.split())


def _optional_url(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be null or an absolute HTTP(S) URL")
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be null or an absolute HTTP(S) URL")
    return value.strip()


def _ids(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must contain evidence IDs")
    result = tuple(dict.fromkeys(item.strip() for item in value))
    if len(result) != len(value):
        raise ValueError(f"{field} must contain unique evidence IDs")
    return result


@dataclass(frozen=True, slots=True)
class AdChannelOutput:
    channel: str
    status: str
    angle: str | None
    offer: str | None
    call_to_action: str | None
    landing_page: str | None
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.channel not in AD_CHANNELS:
            raise ValueError(f"channel must be one of {list(AD_CHANNELS)}")
        if self.status not in AD_STATUSES:
            raise ValueError(f"status must be one of {list(AD_STATUSES)}")
        for name in COPY_FIELDS:
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        object.__setattr__(self, "landing_page", _optional_url(self.landing_page, "landing_page"))
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))

    @property
    def has_copy(self) -> bool:
        return any(getattr(self, name) is not None for name in COPY_FIELDS)


@dataclass(frozen=True, slots=True)
class AdsOutput:
    channels: tuple[AdChannelOutput, ...]

    def __post_init__(self) -> None:
        channels = tuple(self.channels)
        if not all(isinstance(item, AdChannelOutput) for item in channels):
            raise ValueError("channels must contain AdChannelOutput values")
        names = [item.channel for item in channels]
        if len(set(names)) != len(names):
            raise ValueError("each channel may appear at most once")
        object.__setattr__(self, "channels", channels)

    def channel(self, name: str) -> AdChannelOutput | None:
        return next((item for item in self.channels if item.channel == name), None)

    @property
    def cited_evidence_ids(self) -> frozenset[str]:
        return frozenset(ref for item in self.channels for ref in item.evidence_ids)


def parse_ads_output(
    value: Mapping[str, Any], retained_evidence_ids: Iterable[str],
) -> AdsOutput:
    """Parse the top-level payload ``{"ads": {"channels": [...]}, "unknowns": [...]}``."""
    if not isinstance(value, Mapping):
        raise ValueError("ads output must be an object")
    if set(value) - {ADS_FIELD, "unknowns"} or ADS_FIELD not in value:
        raise ValueError("ads output keys must be exactly ads plus optional unknowns")
    ads = value[ADS_FIELD]
    if not isinstance(ads, Mapping) or set(ads) != {"channels"}:
        raise ValueError("ads must be an object with exactly a channels list")
    raw_channels = ads["channels"]
    if not isinstance(raw_channels, (list, tuple)):
        raise ValueError("ads.channels must be a list")
    channels = []
    for item in raw_channels:
        if not isinstance(item, Mapping):
            raise ValueError("each channel entry must be an object")
        extra = set(item) - {"channel", "status", *COPY_FIELDS, "landing_page", "evidence_ids"}
        if extra:
            raise ValueError(f"channel entry has unexpected keys: {sorted(extra)}")
        channels.append(AdChannelOutput(
            item.get("channel"), item.get("status"), item.get("angle"), item.get("offer"),
            item.get("call_to_action"), item.get("landing_page"), item.get("evidence_ids"),
        ))
    unknowns = value.get("unknowns", [])
    if not isinstance(unknowns, (list, tuple)) or any(item != ADS_FIELD for item in unknowns):
        raise ValueError("unknowns may only name ads")
    if bool(channels) == bool(unknowns):
        raise ValueError("ads is unknown exactly when the channels list is empty")
    output = AdsOutput(tuple(channels))
    retained = set(retained_evidence_ids)
    if not output.cited_evidence_ids <= retained:
        raise ValueError("all channels must reference retained Evidence IDs")
    return output


AD_LIBRARY_HOSTS: Mapping[str, tuple[str, ...]] = {
    "google": ("adstransparency.google.com",),
    "meta": ("www.facebook.com", "facebook.com"),
}


def ad_library_channel(url: str) -> str | None:
    """Return the ad channel whose library ``url`` belongs to, else None."""
    lowered = url.lower()
    if lowered.startswith("https://adstransparency.google.com/"):
        return "google"
    if lowered.startswith(("https://www.facebook.com/ads/library", "https://facebook.com/ads/library")):
        return "meta"
    return None


def ad_library_evidence_ids(dossier: CompanyDossier) -> tuple[str, ...]:
    """Evidence IDs captured from an ad library; only these may support a channel."""
    return tuple(
        item.evidence_id for item in dossier.evidence if ad_library_channel(item.url) is not None
    )


def ads_output_contract(dossier: CompanyDossier) -> dict[str, Any]:
    """Strict JSON schema for the model: ``ads.channels`` plus ``unknowns``.

    The ``evidence_ids`` enum is limited to ad-library Evidence so website,
    LinkedIn, or directory Evidence can never support a channel. When the
    dossier holds no ad-library Evidence at all, the enum falls back to every
    retained ID (the prompt then requires an empty channel list).
    """
    evidence_ids = list(ad_library_evidence_ids(dossier)) or [
        item.evidence_id for item in dossier.evidence
    ]
    nullable_text = {"type": ["string", "null"]}
    channel = {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "enum": list(AD_CHANNELS)},
            "status": {"type": "string", "enum": list(AD_STATUSES)},
            "angle": nullable_text,
            "offer": nullable_text,
            "call_to_action": nullable_text,
            "landing_page": nullable_text,
            "evidence_ids": {
                "type": "array", "items": {"type": "string", "enum": evidence_ids},
                "minItems": 1,
            },
        },
        "required": ["channel", "status", "angle", "offer", "call_to_action",
                     "landing_page", "evidence_ids"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            ADS_FIELD: {
                "type": "object",
                "properties": {"channels": {"type": "array", "items": channel}},
                "required": ["channels"],
                "additionalProperties": False,
            },
            "unknowns": {"type": "array", "items": {"type": "string", "enum": [ADS_FIELD]}},
        },
        "required": [ADS_FIELD, "unknowns"],
        "additionalProperties": False,
    }
