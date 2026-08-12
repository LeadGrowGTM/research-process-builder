from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .contracts import EvidenceRef, MAX_EXCERPT_CHARS, canonical_json


@dataclass(frozen=True, slots=True)
class SourceRecord:
    url: str
    retrieved_at: datetime
    source_type: str
    provider: str
    content: str
    excerpt: str
    freshness_days: int
    paid_cost_usd: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source URL must be an absolute HTTP(S) URL")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")
        for name in ("source_type", "provider", "content"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if not isinstance(self.excerpt, str) or len(self.excerpt) > MAX_EXCERPT_CHARS:
            raise ValueError(f"excerpt exceeds {MAX_EXCERPT_CHARS} characters")
        if not isinstance(self.freshness_days, int) or self.freshness_days < 0:
            raise ValueError("freshness_days must be a non-negative integer")
        _decimal_amount(self.paid_cost_usd, "paid_cost_usd")


def _decimal_amount(value: str, name: str) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError(f"{name} must be a decimal amount") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return amount


def _source_payload(source: SourceRecord) -> dict[str, Any]:
    return {
        "content": source.content,
        "excerpt": source.excerpt,
        "freshness_days": source.freshness_days,
        "paid_cost_usd": source.paid_cost_usd,
        "provider": source.provider,
        "retrieved_at": source.retrieved_at.isoformat(),
        "source_type": source.source_type,
        "url": source.url,
    }


def _source_from_payload(payload: dict[str, Any]) -> SourceRecord:
    try:
        return SourceRecord(
            url=payload["url"],
            retrieved_at=datetime.fromisoformat(payload["retrieved_at"]),
            source_type=payload["source_type"],
            provider=payload["provider"],
            content=payload["content"],
            excerpt=payload["excerpt"],
            freshness_days=payload["freshness_days"],
            paid_cost_usd=payload["paid_cost_usd"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("evidence object failed tampering validation") from error


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def cache_key(url: str, provider: str, freshness_days: int) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("url must be non-empty text")
    if not isinstance(provider, str) or not provider:
        raise ValueError("provider must be non-empty text")
    if not isinstance(freshness_days, int) or freshness_days < 0:
        raise ValueError("freshness_days must be a non-negative integer")
    material = canonical_json(
        {"freshness_days": freshness_days, "provider": provider, "url": url}
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class EvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.journal = self.root / "sources.jsonl"

    def put(self, source: SourceRecord) -> EvidenceRef:
        payload = _source_payload(source)
        content_hash = _payload_hash(payload)
        object_path = self.objects / f"{content_hash}.json"
        if object_path.exists():
            stored = self.get(content_hash)
            return self._reference(stored, content_hash)

        self.objects.mkdir(parents=True, exist_ok=True)
        serialized = canonical_json(payload) + "\n"
        temporary = object_path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.replace(temporary, object_path)
        finally:
            temporary.unlink(missing_ok=True)

        self.root.mkdir(parents=True, exist_ok=True)
        event = canonical_json(
            {
                "content_hash": content_hash,
                "evidence_id": self._evidence_id(content_hash),
                "provider": source.provider,
                "retrieved_at": source.retrieved_at,
                "url": source.url,
            }
        )
        with self.journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(event + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return self._reference(source, content_hash)

    def get(self, content_hash: str) -> SourceRecord:
        if (
            not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        object_path = self.objects / f"{content_hash}.json"
        try:
            payload = json.loads(object_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("evidence object failed tampering validation") from error
        if not isinstance(payload, dict) or _payload_hash(payload) != content_hash:
            raise ValueError("evidence object failed tampering validation")
        return _source_from_payload(payload)

    @staticmethod
    def _evidence_id(content_hash: str) -> str:
        return f"ev-{content_hash[:16]}"

    @classmethod
    def _reference(cls, source: SourceRecord, content_hash: str) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=cls._evidence_id(content_hash),
            url=source.url,
            retrieved_at=source.retrieved_at,
            content_hash=content_hash,
            excerpt=source.excerpt,
        )


class SaturationTracker:
    def __init__(self, required_fields: tuple[str, ...]) -> None:
        if not required_fields or any(
            not isinstance(field, str) or not field for field in required_fields
        ):
            raise ValueError("required_fields must contain non-empty field names")
        self._required_fields = frozenset(required_fields)
        self._observed_fields: set[str] = set()
        self._source_counts: Counter[str] = Counter()
        self._consecutive_dry_angles = 0

    def observe_source(self, source_type: str) -> None:
        if source_type not in {"first_party", "independent"}:
            raise ValueError("source_type must be first_party or independent")
        self._source_counts[source_type] += 1

    def observe_field(self, field: str) -> None:
        if not isinstance(field, str) or not field:
            raise ValueError("field must be non-empty text")
        self._observed_fields.add(field)

    def observe_search_angle(self, *, material_facts_added: bool) -> None:
        if not isinstance(material_facts_added, bool):
            raise ValueError("material_facts_added must be boolean")
        self._consecutive_dry_angles = (
            0 if material_facts_added else self._consecutive_dry_angles + 1
        )

    @property
    def is_saturated(self) -> bool:
        return (
            self._required_fields <= self._observed_fields
            and self._source_counts["first_party"] >= 1
            and self._source_counts["independent"] >= 2
            and self._consecutive_dry_angles >= 2
        )
