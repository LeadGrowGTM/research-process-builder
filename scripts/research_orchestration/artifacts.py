"""Local, canonical, fail-closed persistence for autoresearch run artifacts."""

from __future__ import annotations
from contextlib import contextmanager
from threading import Lock, RLock

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .budgets import BudgetLimits
from .contracts import Role, RoleEnvelope, RunRequest, SCHEMA_VERSION, SchemaError


_FORBIDDEN_FIELD_PARTS = (
    "secret", "token", "password", "authorization", "transcript", "api_key", "credential",
)
_STAGES = tuple(role.value for role in Role)
_GATE_STAGE = "gate"
_NEXT_STAGE = dict(zip(("start",) + _STAGES, _STAGES + (_GATE_STAGE,)))
_LOCKS_GUARD = Lock()
_RUN_LOCKS: dict[str, RLock] = {}



class ArtifactHaltForReview(RuntimeError):
    """A persisted-state ambiguity which must be reviewed rather than repaired."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ResumeCursor:
    """The next unrecorded role and work already made idempotent by artifacts."""

    cycle: int
    stage: str
    completed_idempotency_keys: frozenset[str]


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ArtifactHaltForReview("noncanonical_artifact") from error


def _require_safe_value(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ArtifactHaltForReview("invalid_artifact")
            normalized = key.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
            _require_safe_value(nested)
    elif isinstance(value, list):
        for nested in value:
            _require_safe_value(nested)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        return
    else:
        raise ArtifactHaltForReview("invalid_artifact")



def _locked_method(method):
    def wrapped(self, *args, **kwargs):
        with self._locked():
            return method(self, *args, **kwargs)

    return wrapped

class ArtifactStore:
    """A deliberately small filesystem seam; it has no remote or execution behavior."""

    def __init__(self, root: str | Path):
        self._root = Path(root)

    @contextmanager
    def _locked(self):
        """Serialize local writers in-process and across processes for this run root."""
        key = str(self._root.absolute())
        with _LOCKS_GUARD:
            lock = _RUN_LOCKS.setdefault(key, RLock())
        with lock:
            self._root.mkdir(parents=True, exist_ok=True)
            lock_path = self._root / "run.lock"
            if lock_path.exists() and (not lock_path.is_file() or lock_path.is_symlink()):
                raise ArtifactHaltForReview("lock_io_failed")
            with lock_path.open("a+b") as handle:
                handle.seek(0)
                if not handle.read(1):
                    handle.seek(0)
                    handle.write(b"0")
                    handle.flush()
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    yield
                finally:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @property
    def _run_path(self) -> Path:
        return self._root / "run.json"

    @property
    def _journal_path(self) -> Path:
        return self._root / "journal.jsonl"

    def create_run(self, request: RunRequest) -> Path:
        with self._locked():
            return self._create_run(request)

    def _create_run(self, request: RunRequest) -> Path:
        if not isinstance(request, RunRequest):
            raise ArtifactHaltForReview("invalid_run_request")
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "objects").mkdir(exist_ok=True)
        (self._root / "cycles").mkdir(exist_ok=True)
        payload = request.to_canonical_dict()
        self._write_immutable(self._run_path, _canonical_bytes(payload), "run_collision")
        return self._root

    @_locked_method
    def put_role_artifact(
        self,
        cycle: int,
        role: Role,
        envelope: RoleEnvelope | Mapping[str, Any],
        idempotency_key: str,
    ) -> str:
        self._require_initialized()
        self._require_cycle(cycle)
        self._require_idempotency_key(idempotency_key)
        if isinstance(envelope, Mapping):
            _require_safe_value(envelope)
            raise ArtifactHaltForReview("invalid_role_artifact")
        if not isinstance(envelope, RoleEnvelope) or not isinstance(role, Role) or envelope.role is not role:
            raise ArtifactHaltForReview("invalid_role_artifact")
        payload = envelope.to_canonical_dict()
        _require_safe_value(payload)
        object_bytes = _canonical_bytes(payload)
        artifact_hash = sha256(object_bytes).hexdigest()
        cycle_path = self._cycle_path(cycle)
        cycle_path.mkdir(parents=True, exist_ok=True)
        reference_path = cycle_path / f"{role.value}.json"
        if reference_path.exists():
            existing = self._read_json(reference_path, "invalid_cycle_reference")
            if existing.get("idempotency_key") != idempotency_key or existing.get("stage") != role.value:
                raise ArtifactHaltForReview("idempotency_collision")
            existing_hash = existing.get("artifact_hash")
            if not isinstance(existing_hash, str):
                raise ArtifactHaltForReview("invalid_cycle_reference")
            self._validate_object(existing_hash)
            return existing_hash
        for existing in self._all_references():
            if existing["idempotency_key"] == idempotency_key:
                raise ArtifactHaltForReview("idempotency_collision")
        self._write_immutable(self._object_path(artifact_hash), object_bytes, "artifact_collision")
        reference = {
            "artifact_hash": artifact_hash,
            "cycle": cycle,
            "idempotency_key": idempotency_key,
            "schema_version": SCHEMA_VERSION,
            "stage": role.value,
        }
        self._write_immutable(reference_path, _canonical_bytes(reference), "artifact_reference_collision")
        return artifact_hash

    @_locked_method
    def append_transition(
        self,
        cycle: int,
        from_stage: str,
        to_stage: str,
        artifact_hash: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        self._require_initialized()
        self._require_cycle(cycle)
        self._require_stage(from_stage, allow_start=True)
        self._require_stage(to_stage)
        self._require_idempotency_key(idempotency_key)
        rows = self._journal_rows()
        cycle_rows = [row for row in rows if row["cycle"] == cycle]
        if from_stage != (cycle_rows[-1]["to_stage"] if cycle_rows else "start"):
            raise ArtifactHaltForReview("invalid_transition")
        if _NEXT_STAGE.get(from_stage) != to_stage:
            raise ArtifactHaltForReview("invalid_transition")
        matches = [
            reference for reference in self._all_references()
            if reference.get("cycle") == cycle and reference.get("stage") == to_stage
            and reference.get("artifact_hash") == artifact_hash and reference.get("idempotency_key") == idempotency_key
        ]
        if len(matches) != 1:
            raise ArtifactHaltForReview("journal_reference_mismatch")
        if any(row["idempotency_key"] == idempotency_key for row in self._journal_rows()):
            raise ArtifactHaltForReview("idempotency_collision")
        self._validate_object(artifact_hash)
        row = {
            "artifact_hash": artifact_hash,
            "cycle": cycle,
            "from_stage": from_stage,
            "idempotency_key": idempotency_key,
            "schema_version": SCHEMA_VERSION,
            "sequence": len(rows) + 1,
            "to_stage": to_stage,
        }
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._journal_path.open("a", encoding="utf-8", newline="\n") as journal:
            journal.write(_canonical_bytes(row).decode("utf-8") + "\n")
            journal.flush()
            os.fsync(journal.fileno())
        return row

    def load_and_validate(self) -> Mapping[str, Any]:
        self._require_initialized()
        self._validate_run_tree()
        run = self._read_json(self._run_path, "invalid_run")
        self._validate_schema(run)
        _require_safe_value(run)
        self._validate_run(run)
        references = self._all_references()
        object_hashes = self._validate_all_objects()
        if object_hashes != {reference["artifact_hash"] for reference in references}:
            raise ArtifactHaltForReview("unreferenced_object")
        for reference in references:
            self._validate_reference(reference)
            self._validate_object(reference["artifact_hash"])
        rows = self._journal_rows()
        self._validate_relationships(references, rows)
        return run

    def resume_cursor(self) -> ResumeCursor:
        self.load_and_validate()
        references = self._all_references()
        completed = frozenset(reference["idempotency_key"] for reference in references)
        by_cycle: dict[int, set[str]] = {}
        for reference in references:
            by_cycle.setdefault(reference["cycle"], set()).add(reference["stage"])
        highest_cycle = max(by_cycle, default=0)
        for cycle in range(highest_cycle + 1):
            completed_stages = by_cycle.get(cycle, set())
            for stage in _STAGES:
                if stage not in completed_stages:
                    return ResumeCursor(cycle, stage, completed)
        return ResumeCursor(highest_cycle + 1, _STAGES[0], completed)

    def project_summary(self) -> Mapping[str, Any]:
        run = self.load_and_validate()
        cycles: list[dict[str, Any]] = []
        for cycle in sorted({reference["cycle"] for reference in self._all_references()}):
            artifacts = [
                {
                    "artifact_hash": reference["artifact_hash"],
                    "idempotency_key": reference["idempotency_key"],
                    "stage": reference["stage"],
                }
                for reference in self._cycle_references(cycle)
            ]
            cycles.append({"artifacts": artifacts, "cycle": cycle})
        summary = {
            "cycles": cycles,
            "journal_sequences": [row["sequence"] for row in self._journal_rows()],
            "run_id": run["run_id"],
            "schema_version": SCHEMA_VERSION,
        }
        self._atomic_replace(self._root / "summary.json", _canonical_bytes(summary))
        return summary

    def _validate_run_tree(self) -> None:
        allowed = {"run.json", "journal.jsonl", "summary.json", "objects", "cycles", "run.lock"}
        for item in self._root.iterdir():
            normalized = item.name.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
            if item.name not in allowed:
                raise ArtifactHaltForReview("unexpected_artifact_file")
        if not (self._root / "objects").is_dir() or not (self._root / "cycles").is_dir():
            raise ArtifactHaltForReview("invalid_run_tree")
        lock_path = self._root / "run.lock"
        if lock_path.exists() and (not lock_path.is_file() or lock_path.is_symlink()):
            raise ArtifactHaltForReview("unsafe_lock_path")

    def _require_initialized(self) -> None:
        if not self._run_path.is_file():
            raise ArtifactHaltForReview("missing_run")

    @staticmethod
    def _require_cycle(cycle: int) -> None:
        if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 0:
            raise ArtifactHaltForReview("invalid_cycle")

    @staticmethod
    def _require_idempotency_key(value: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 4_000:
            raise ArtifactHaltForReview("invalid_idempotency_key")

    @staticmethod
    def _require_stage(value: str, *, allow_start: bool = False) -> None:
        if value == "start" and allow_start:
            return
        if value not in _STAGES:
            raise ArtifactHaltForReview("invalid_stage")

    def _cycle_path(self, cycle: int) -> Path:
        return self._root / "cycles" / str(cycle)

    def _object_path(self, artifact_hash: str) -> Path:
        if not isinstance(artifact_hash, str) or len(artifact_hash) != 64 or any(char not in "0123456789abcdef" for char in artifact_hash):
            raise ArtifactHaltForReview("invalid_artifact_hash")
        return self._root / "objects" / f"{artifact_hash}.json"

    def _atomic_replace(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
        except OSError as error:
            raise ArtifactHaltForReview("atomic_write_failed") from error
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _write_immutable(self, path: Path, content: bytes, collision_reason: str) -> None:
        if path.exists():
            try:
                current = path.read_bytes()
            except OSError as error:
                raise ArtifactHaltForReview("artifact_read_failed") from error
            if current != content:
                raise ArtifactHaltForReview(collision_reason)
            return
        self._atomic_replace(path, content)

    def _read_json(self, path: Path, reason: str) -> dict[str, Any]:
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactHaltForReview(reason) from error
        if not isinstance(decoded, dict):
            raise ArtifactHaltForReview(reason)
        if _canonical_bytes(decoded) != path.read_bytes():
            raise ArtifactHaltForReview(reason)
        return decoded

    @staticmethod
    def _validate_schema(value: Mapping[str, Any]) -> None:
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ArtifactHaltForReview("unsupported_schema_version")

    @staticmethod
    def _validate_run(value: Mapping[str, Any]) -> None:
        try:
            budget_limits = BudgetLimits(**value["budget_limits"])
            run = RunRequest(
                schema_version=value["schema_version"],
                run_id=value["run_id"],
                brief=value["brief"],
                constraints=tuple(value["constraints"]),
                baseline=value["baseline"],
                budget_limits=budget_limits,
                approval_threshold=value["approval_threshold"],
            )
        except (KeyError, TypeError, SchemaError) as error:
            raise ArtifactHaltForReview("invalid_run") from error
        if run.to_canonical_dict() != value:
            raise ArtifactHaltForReview("invalid_run")

    def _validate_all_objects(self) -> set[str]:
        object_root = self._root / "objects"
        object_hashes: set[str] = set()
        if not object_root.is_dir():
            raise ArtifactHaltForReview("missing_objects")
        for item in object_root.iterdir():
            if not item.is_file() or item.suffix != ".json":
                raise ArtifactHaltForReview("invalid_object_path")
            self._validate_object(item.stem)
            object_hashes.add(item.stem)
        return object_hashes

    def _validate_object(self, artifact_hash: str) -> None:
        path = self._object_path(artifact_hash)
        try:
            content = path.read_bytes()
            value = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactHaltForReview("missing_or_invalid_object") from error
        if sha256(content).hexdigest() != artifact_hash:
            raise ArtifactHaltForReview("artifact_hash_mismatch")
        if not isinstance(value, dict) or _canonical_bytes(value) != content:
            raise ArtifactHaltForReview("noncanonical_artifact")
        _require_safe_value(value)
        self._validate_schema(value)
        try:
            RoleEnvelope.rehydrate(
                schema_version=value["schema_version"],
                role=Role(value["role"]),
                invocation_id=value["invocation_id"],
                payload=value["payload"],
            )
        except (KeyError, TypeError, ValueError, SchemaError) as error:
            raise ArtifactHaltForReview("invalid_role_artifact") from error

    def _cycle_references(self, cycle: int) -> list[dict[str, Any]]:
        path = self._cycle_path(cycle)
        if not path.exists():
            return []
        if not path.is_dir():
            raise ArtifactHaltForReview("invalid_cycle_reference")
        references: list[dict[str, Any]] = []
        for item in sorted(path.iterdir(), key=lambda entry: entry.name):
            normalized = item.name.casefold().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ArtifactHaltForReview("sensitive_content")
            if not item.is_file() or item.suffix != ".json" or item.stem not in _STAGES:
                raise ArtifactHaltForReview("unexpected_artifact_file")
            reference = self._read_json(item, "invalid_cycle_reference")
            if reference.get("cycle") != cycle or reference.get("stage") != item.stem:
                raise ArtifactHaltForReview("invalid_cycle_reference")
            references.append(reference)
        return references

    def _all_references(self) -> list[dict[str, Any]]:
        cycles_root = self._root / "cycles"
        if not cycles_root.exists() or not cycles_root.is_dir():
            raise ArtifactHaltForReview("missing_cycles")
        references: list[dict[str, Any]] = []
        for directory in sorted(cycles_root.iterdir(), key=lambda item: item.name):
            if not directory.is_dir() or not directory.name.isdecimal() or directory.name != str(int(directory.name)):
                raise ArtifactHaltForReview("invalid_cycle_reference")
            references.extend(self._cycle_references(int(directory.name)))
        return references

    def _validate_reference(self, reference: Mapping[str, Any]) -> None:
        self._validate_schema(reference)
        expected = {"artifact_hash", "cycle", "idempotency_key", "schema_version", "stage"}
        if set(reference) != expected:
            raise ArtifactHaltForReview("invalid_cycle_reference")
        self._require_cycle(reference["cycle"])
        self._require_stage(reference["stage"])
        self._require_idempotency_key(reference["idempotency_key"])
        self._object_path(reference["artifact_hash"])

    def _validate_relationships(self, references: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
        by_key: dict[str, dict[str, Any]] = {}
        for reference in references:
            key = reference["idempotency_key"]
            if key in by_key:
                raise ArtifactHaltForReview("idempotency_collision")
            value = self._read_json(self._object_path(reference["artifact_hash"]), "missing_or_invalid_object")
            if value.get("role") != reference["stage"]:
                raise ArtifactHaltForReview("role_artifact_mismatch")
            by_key[key] = reference
        seen_rows: set[str] = set()
        for row in rows:
            key = row["idempotency_key"]
            if key in seen_rows:
                raise ArtifactHaltForReview("idempotency_collision")
            seen_rows.add(key)
            if _NEXT_STAGE.get(row["from_stage"]) != row["to_stage"]:
                raise ArtifactHaltForReview("invalid_transition")
            reference = by_key.get(key)
            if reference is None:
                raise ArtifactHaltForReview("journal_reference_mismatch")
            if any(reference[field] != row[field] for field in ("cycle", "stage", "artifact_hash") if field != "stage") or reference["stage"] != row["to_stage"]:
                raise ArtifactHaltForReview("journal_reference_mismatch")
        if set(by_key) != seen_rows:
            raise ArtifactHaltForReview("unlinked_artifact_reference")

    def _journal_rows(self) -> list[dict[str, Any]]:
        if not self._journal_path.exists():
            return []
        try:
            lines = self._journal_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise ArtifactHaltForReview("invalid_journal") from error
        raw = self._journal_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ArtifactHaltForReview("truncated_journal")
        rows: list[dict[str, Any]] = []
        for expected_sequence, line in enumerate(lines, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ArtifactHaltForReview("truncated_journal") from error
            if not isinstance(row, dict) or _canonical_bytes(row).decode("utf-8") != line:
                raise ArtifactHaltForReview("invalid_journal")
            expected_fields = {
                "artifact_hash", "cycle", "from_stage", "idempotency_key", "schema_version", "sequence", "to_stage",
            }
            if set(row) != expected_fields or row["sequence"] != expected_sequence:
                raise ArtifactHaltForReview("invalid_journal_sequence")
            self._validate_schema(row)
            self._require_cycle(row["cycle"])
            self._require_stage(row["from_stage"], allow_start=True)
            self._require_stage(row["to_stage"])
            self._require_idempotency_key(row["idempotency_key"])
            self._validate_object(row["artifact_hash"])
            rows.append(row)
        return rows
