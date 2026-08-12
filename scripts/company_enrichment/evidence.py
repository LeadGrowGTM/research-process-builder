from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlparse

from ._locking import file_lock
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


@dataclass(frozen=True, slots=True)
class SearchAngleResult:
    material_facts_added: bool
    angle_id: str | None = None
    source_id: str | None = None
    source_type: str | None = None
    field_citations: tuple[tuple[str, str], ...] = ()
    unavailable_source_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.material_facts_added, bool):
            raise ValueError("material_facts_added must be boolean")
        if (self.source_id is None) != (self.source_type is None):
            raise ValueError("source_id and source_type must be recorded together")
        if self.source_type not in {None, "first_party", "independent"}:
            raise ValueError("source_type must be first_party or independent")
        if not self.material_facts_added and not self.angle_id:
            raise ValueError("dry search results require an angle_id")
        object.__setattr__(self, "field_citations", tuple(self.field_citations))
        object.__setattr__(self, "unavailable_source_types", tuple(self.unavailable_source_types))
        if any(not field or not evidence_id for field, evidence_id in self.field_citations):
            raise ValueError("field citations require field and evidence IDs")
        if any(item not in {"first_party", "independent"} for item in self.unavailable_source_types):
            raise ValueError("unknown unavailable source type")


def _decimal_amount(value: str, name: str) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError(f"{name} must be a decimal amount") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return amount


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _event_body(source: SourceRecord, content_hash: str) -> dict[str, Any]:
    return {
        "content_hash": content_hash,
        "excerpt": source.excerpt,
        "freshness_days": source.freshness_days,
        "paid_cost_usd": source.paid_cost_usd,
        "provider": source.provider,
        "retrieved_at": source.retrieved_at.isoformat(),
        "source_type": source.source_type,
        "url": source.url,
    }


def _event_id(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


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
        self.cache_journal = self.root / "cache.jsonl"
        self._lock_path = self.root / ".evidence.lock"

    def put(self, source: SourceRecord) -> EvidenceRef:
        content_hash = _content_hash(source.content)
        with file_lock(self._lock_path):
            self.objects.mkdir(parents=True, exist_ok=True)
            object_path = self.objects / f"{content_hash}.json"
            if object_path.exists():
                self._read_content(content_hash)
            else:
                self._write_object(object_path, {"content": source.content})

            body = _event_body(source, content_hash)
            event_id = _event_id(body)
            existing_ids = {event["event_id"] for event in self._read_events()}
            if event_id not in existing_ids:
                event = {**body, "event_id": event_id}
                self._append(self.journal, event)
            cache_body = {
                "cache_key": cache_key(source.url, source.provider, source.freshness_days),
                "content_hash": content_hash,
                "freshness_days": source.freshness_days,
                "provider": source.provider,
                "retrieved_at": source.retrieved_at.isoformat(),
                "url": source.url,
            }
            cache_event_id = _event_id(cache_body)
            existing_cache_ids = {
                event["event_id"] for event in self._read_cache_events()
            }
            if cache_event_id not in existing_cache_ids:
                self._append(
                    self.cache_journal,
                    {**cache_body, "event_id": cache_event_id},
                )
        return self._reference(source, content_hash)

    def resolve(
        self,
        *,
        url: str,
        provider: str,
        freshness_days: int,
        as_of: datetime,
        collect: Callable[[], SourceRecord],
    ) -> tuple[EvidenceRef, bool]:
        cached = self.lookup(
            url=url,
            provider=provider,
            freshness_days=freshness_days,
            as_of=as_of,
        )
        if cached is not None:
            return self._reference(cached, _content_hash(cached.content)), True
        source = collect()
        if (
            source.url != url
            or source.provider != provider
            or source.freshness_days != freshness_days
        ):
            raise ValueError("collected source does not match the requested cache identity")
        return self.put(source), False

    def lookup(
        self,
        *,
        url: str,
        provider: str,
        freshness_days: int,
        as_of: datetime,
    ) -> SourceRecord | None:
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        identity = cache_key(url, provider, freshness_days)
        with file_lock(self._lock_path):
            matches = [
                event
                for event in self._read_cache_events()
                if event["cache_key"] == identity
            ]
            if not matches:
                return None
            latest = max(matches, key=lambda item: item["retrieved_at"])
            retrieved_at = datetime.fromisoformat(latest["retrieved_at"])
            if retrieved_at + timedelta(days=freshness_days) < as_of:
                return None
            content = self._read_content(latest["content_hash"])
            source_event = next(
                (
                    event
                    for event in reversed(self._read_events())
                    if event["content_hash"] == latest["content_hash"]
                    and event["url"] == url
                    and event["provider"] == provider
                    and event["retrieved_at"] == latest["retrieved_at"]
                ),
                None,
            )
            if source_event is None:
                raise ValueError("cache entry has no matching source observation")
            return self._record_from_event(source_event, content)

    def get(self, content_hash: str) -> SourceRecord:
        self._validate_hash(content_hash)
        with file_lock(self._lock_path):
            content = self._read_content(content_hash)
            event = next(
                (item for item in self._read_events() if item["content_hash"] == content_hash),
                None,
            )
            if event is None:
                raise ValueError("evidence object has no source observation")
            return self._record_from_event(event, content)

    def _read_content(self, content_hash: str) -> str:
        self._validate_hash(content_hash)
        object_path = self.objects / f"{content_hash}.json"
        try:
            payload = json.loads(object_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"content"}:
                raise ValueError("invalid content object")
            content = payload["content"]
            if not isinstance(content, str) or _content_hash(content) != content_hash:
                raise ValueError("content hash mismatch")
            return content
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("evidence object failed tampering validation") from error

    def _read_events(self) -> tuple[dict[str, Any], ...]:
        if not self.journal.exists():
            return ()
        events = []
        for line_number, line in enumerate(self.journal.read_text(encoding="utf-8").splitlines(), 1):
            try:
                event = json.loads(line)
                event_id = event.pop("event_id")
                if not isinstance(event, dict) or event_id != _event_id(event):
                    raise ValueError("source event hash mismatch")
                events.append({**event, "event_id": event_id})
            except (KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"invalid evidence journal event on line {line_number}") from error
        return tuple(events)

    def _read_cache_events(self) -> tuple[dict[str, Any], ...]:
        if not self.cache_journal.exists():
            return ()
        events = []
        for line_number, line in enumerate(
            self.cache_journal.read_text(encoding="utf-8").splitlines(), 1
        ):
            try:
                event = json.loads(line)
                event_id = event.pop("event_id")
                if not isinstance(event, dict) or event_id != _event_id(event):
                    raise ValueError("cache event hash mismatch")
                if event["cache_key"] != cache_key(
                    event["url"], event["provider"], event["freshness_days"]
                ):
                    raise ValueError("cache identity mismatch")
                self._validate_hash(event["content_hash"])
                events.append({**event, "event_id": event_id})
            except (KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"invalid cache journal event on line {line_number}") from error
        return tuple(events)

    @staticmethod
    def _record_from_event(event: dict[str, Any], content: str) -> SourceRecord:
        try:
            return SourceRecord(
                url=event["url"],
                retrieved_at=datetime.fromisoformat(event["retrieved_at"]),
                source_type=event["source_type"],
                provider=event["provider"],
                content=content,
                excerpt=event["excerpt"],
                freshness_days=event["freshness_days"],
                paid_cost_usd=event["paid_cost_usd"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("evidence source failed tampering validation") from error

    @staticmethod
    def _append(path: Path, event: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _write_object(path: Path, payload: dict[str, Any]) -> None:
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_name = stream.name
                stream.write(canonical_json(payload) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _validate_hash(content_hash: str) -> None:
        if (
            not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")

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
        self._field_evidence: dict[str, set[str]] = {}
        self._sources: dict[str, set[str]] = {"first_party": set(), "independent": set()}
        self._unavailable_source_types: set[str] = set()
        self._consecutive_dry_angles: list[str] = []

    def observe(self, result: SearchAngleResult) -> None:
        if result.source_id and result.source_type:
            self._sources[result.source_type].add(result.source_id)
        for field, evidence_id in result.field_citations:
            self._field_evidence.setdefault(field, set()).add(evidence_id)
        self._unavailable_source_types.update(result.unavailable_source_types)
        if result.material_facts_added:
            self._consecutive_dry_angles.clear()
        elif result.angle_id not in self._consecutive_dry_angles:
            self._consecutive_dry_angles.append(result.angle_id)

    @property
    def is_saturated(self) -> bool:
        fields_cited = all(self._field_evidence.get(field) for field in self._required_fields)
        first_party_complete = (
            bool(self._sources["first_party"])
            or "first_party" in self._unavailable_source_types
        )
        independent_complete = (
            len(self._sources["independent"]) >= 2
            or "independent" in self._unavailable_source_types
        )
        return (
            fields_cited
            and first_party_complete
            and independent_complete
            and len(self._consecutive_dry_angles) >= 2
        )
