from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from urllib.parse import urlparse
import io
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping, Sequence

from .benchmark import ExecutionTrack
from .contracts import FieldAssertion, Visibility, canonical_json
from .executors import P0_ENRICHMENTS
from .experiment_runner import ExperimentInput, ModelExecution
from .icp_persona_contracts import parse_icp_output


_MILLION = Decimal("1000000")
_DEFAULT_MAX_OUTPUT_TOKENS = 1_024
_MODEL_MAX_OUTPUT_TOKENS = {"gpt-5-nano": 4_096}
# Field-keyed signal contracts (news events, competitor sets) legitimately run
# to twenty-plus cited entries; 1,024 tokens truncates them into invalid JSON.
_FIELD_KEYED_MAX_OUTPUT_TOKENS = 4_096
# Field-keyed signal requests pin sampling so two lineages that differ only in
# prompt text are comparable. Reasoning models reject ``temperature``.
_FIELD_KEYED_TEMPERATURE = 0
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_SECRET_PATTERN = re.compile(
    r"(?i)(?:sk|sess|key)-[a-z0-9_-]{8,}|bearer\s+[a-z0-9._-]+"
)


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    batch_input_per_million: Decimal
    batch_cached_input_per_million: Decimal
    batch_output_per_million: Decimal


# OpenAI list prices in USD per one million text tokens. Batch is 50% of the
# standard rate. Decimal literals keep the budget ledger free of float drift.
# Only these models may run (workspace policy, Mitch 2026-08-18): gpt-4.1-mini,
# gpt-5-nano, and the flagship gpt-5.6-luna. The full gpt-4.1 tier and the
# gpt-4o family are never used and are deliberately absent, so a request for
# them fails validation before any spend. Do not add models without approval.
MODEL_PRICES: Mapping[str, ModelPrice] = {
    "gpt-5-nano": ModelPrice(
        Decimal("0.05"), Decimal("0.005"), Decimal("0.40"),
        Decimal("0.025"), Decimal("0.0025"), Decimal("0.20"),
    ),
    "gpt-4.1-mini": ModelPrice(
        Decimal("0.40"), Decimal("0.10"), Decimal("1.60"),
        Decimal("0.20"), Decimal("0.05"), Decimal("0.80"),
    ),
    "gpt-5.6-luna": ModelPrice(
        Decimal("1.00"), Decimal("0.10"), Decimal("6.00"),
        Decimal("0.50"), Decimal("0.05"), Decimal("3.00"),
    ),
}


class OpenAIProviderError(RuntimeError):
    """A provider failure whose message is safe to persist or display."""


class OpenAIBatchPending(BaseException):
    """A durable provider job is still running and must keep its reservation."""


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(data + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("provider state must be a JSON object")
    return value


def _safe_provider_value(value: Any) -> Any:
    """Return JSON-safe provider data with credential-shaped material removed."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                normalized in {
                    "api_key", "apikey", "authorization", "input",
                    "instructions", "password", "secret", "token",
                }
                or normalized.endswith("_key")
                or normalized.endswith("_secret")
                or normalized.endswith("_token")
            ):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _safe_provider_value(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_safe_provider_value(item) for item in value]
    if isinstance(value, str):
        return _SECRET_PATTERN.sub("[redacted]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _response_payload(response: Any) -> Mapping[str, Any]:
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            value = dump(mode="json")
        except TypeError:
            value = dump()
        if isinstance(value, Mapping):
            return _safe_provider_value(value)
    if isinstance(response, Mapping):
        return _safe_provider_value(response)
    return {
        "id": _safe_provider_value(getattr(response, "id", None)),
        "model": _safe_provider_value(getattr(response, "model", None)),
        "output_text": _safe_provider_value(getattr(response, "output_text", None)),
    }


def _attribute(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


class OpenAIModelClient:
    """Responses/Batch adapter for the company enrichment Experiment seam."""

    def __init__(
        self,
        *,
        artifact_root: Path,
        sdk_client: Any,
        poll_interval_seconds: float = 2.0,
        max_poll_seconds: float = 86_400.0,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        if sdk_client is None:
            raise ValueError("sdk_client is required")
        if poll_interval_seconds < 0 or max_poll_seconds < 0:
            raise ValueError("poll timing must be non-negative")
        self._root = Path(artifact_root) / "openai"
        self._sdk = sdk_client
        self._poll_interval = poll_interval_seconds
        self._max_poll = max_poll_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    def estimate(
        self, requests: Sequence[ExperimentInput], track: ExecutionTrack,
    ) -> str:
        requests = self._validated_requests(requests)
        input_rate, _cached_rate, output_rate = self._price(
            requests[0].requested_model_id, track,
        )
        # UTF-8 bytes conservatively bound tokens for the full submitted body,
        # including the structured-output schema. A fixed per-request allowance
        # covers provider framing that is not represented in the JSON payload.
        input_tokens = sum(
            len(canonical_json(self._body(item)).encode("utf-8")) + 1_024
            for item in requests
        )
        output_tokens = sum(
            self._max_output_tokens(item.requested_model_id, item)
            for item in requests
        )
        return str(
            Decimal(input_tokens) * input_rate / _MILLION
            + Decimal(output_tokens) * output_rate / _MILLION
        )

    def execute(
        self, requests: Sequence[ExperimentInput], track: ExecutionTrack,
    ) -> tuple[ModelExecution, ...]:
        requests = self._validated_requests(requests)
        if track is ExecutionTrack.SYNCHRONOUS:
            return tuple(self._execute_sync(request) for request in requests)
        if track is ExecutionTrack.BATCH:
            return self._execute_batch(requests)
        raise ValueError("unsupported execution track")

    def has_pending(
        self, requests: Sequence[ExperimentInput], track: ExecutionTrack,
    ) -> bool:
        if track is not ExecutionTrack.BATCH:
            return False
        values = self._validated_requests(requests)
        state = _read_json(self._batch_path(values))
        return state is not None and state.get("status") in {
            "uploaded", "pending", "received",
        }

    @staticmethod
    def _validated_requests(
        requests: Sequence[ExperimentInput],
    ) -> tuple[ExperimentInput, ...]:
        values = tuple(requests)
        if not values or not all(isinstance(item, ExperimentInput) for item in values):
            raise ValueError("requests must contain ExperimentInput values")
        models = {item.requested_model_id for item in values}
        if len(models) != 1:
            raise ValueError("one provider execution requires one exact model ID")
        if next(iter(models)) not in MODEL_PRICES:
            raise ValueError("unsupported exact OpenAI model ID")
        for item in values:
            if item.enrichment_id not in P0_ENRICHMENTS:
                raise ValueError("unsupported enrichment ID")
            if item.company_id != item.dossier.company_id:
                raise ValueError("request and dossier company IDs must match")
            if not item.dossier.evidence:
                raise ValueError("model execution requires dossier Evidence")
        return values

    @staticmethod
    def _price(
        model: str, track: ExecutionTrack,
    ) -> tuple[Decimal, Decimal, Decimal]:
        price = MODEL_PRICES[model]
        if track is ExecutionTrack.SYNCHRONOUS:
            return (
                price.input_per_million, price.cached_input_per_million,
                price.output_per_million,
            )
        if track is ExecutionTrack.BATCH:
            return (
                price.batch_input_per_million,
                price.batch_cached_input_per_million,
                price.batch_output_per_million,
            )
        raise ValueError("unsupported execution track")

    @staticmethod
    def _is_field_keyed(request: "ExperimentInput | None") -> bool:
        """Signal enrichments (news, competitors, ads) use the field-keyed
        contract path; ICP/persona and legacy flat requests do not."""
        return (
            request is not None
            and request.output_contract is not None
            and request.enrichment_id != "icp-persona-analysis"
        )

    @staticmethod
    def _max_output_tokens(model: str, request: "ExperimentInput | None" = None) -> int:
        limit = _MODEL_MAX_OUTPUT_TOKENS.get(model, _DEFAULT_MAX_OUTPUT_TOKENS)
        if OpenAIModelClient._is_field_keyed(request):
            return max(limit, _FIELD_KEYED_MAX_OUTPUT_TOKENS)
        return limit

    @staticmethod
    def _override_metadata(request: ExperimentInput) -> dict[str, str]:
        if not (
            request.prompt_id or request.prompt_text
            or request.output_contract is not None
        ):
            return {}
        return {
            "prompt_id": request.prompt_id,
            "prompt_sha256": sha256(request.prompt_text.encode("utf-8")).hexdigest(),
            "output_contract_sha256": sha256(
                canonical_json(request.output_contract).encode("utf-8")
            ).hexdigest(),
        }

    @classmethod
    def _request_metadata(cls, request: ExperimentInput) -> dict[str, str]:
        return {
            "company_id": request.company_id,
            "enrichment_id": request.enrichment_id,
            "requested_model_id": request.requested_model_id,
            **cls._override_metadata(request),
        }

    @staticmethod
    def _subject_line(request: ExperimentInput) -> str:
        """Field-keyed signal requests name the subject so the model does not
        confuse it with a similarly named company in the Evidence. The subject
        is the dossier's ``identity`` assertion plus the host of its first base
        Evidence URL; the line is omitted for ICP and legacy requests."""
        if not OpenAIModelClient._is_field_keyed(request):
            return ""
        dossier = request.dossier
        name = next((str(item.value) for item in dossier.assertions
                     if item.field == "identity" and item.value), None)
        host = urlparse(dossier.evidence[0].url).netloc.lower() if dossier.evidence else ""
        host = host.split("@")[-1].split(":")[0]
        host = host[4:] if host.startswith("www.") else host
        if not name and not host:
            return ""
        label = f"{name} ({host})" if name and host else (name or host)
        return f"Subject company: {label}\n"

    @staticmethod
    def _prompt(request: ExperimentInput) -> str:
        evidence = [
            {
                "content_hash": item.content_hash,
                "evidence_id": item.evidence_id,
                "excerpt": item.excerpt,
                "retrieved_at": item.retrieved_at.isoformat(),
                "url": item.url,
            }
            for item in request.dossier.evidence
        ]
        fields = P0_ENRICHMENTS[request.enrichment_id]
        if request.prompt_text:
            return (
                f"{request.prompt_text.rstrip()}\n\n"
                f"Company ID: {request.company_id}\n"
                f"{OpenAIModelClient._subject_line(request)}"
                f"Enrichment: {request.enrichment_id}\n"
                f"Requested fields: {json.dumps(fields)}\n"
                f"Evidence: {json.dumps(evidence, ensure_ascii=False, sort_keys=True)}"
            )
        return (
            "Produce the requested company enrichment using only the supplied Evidence. "
            "Do not infer unsupported facts. Cite one or more supplied evidence_id values "
            "for every assertion; put unsupported requested fields in unknowns. "
            f"Company ID: {request.company_id}\n"
            f"Enrichment: {request.enrichment_id}\n"
            f"Requested fields: {json.dumps(fields)}\n"
            f"Evidence: {json.dumps(evidence, ensure_ascii=False, sort_keys=True)}"
        )

    @staticmethod
    def _schema(request: ExperimentInput) -> dict[str, Any]:
        if request.output_contract is not None:
            return json.loads(canonical_json(request.output_contract))
        fields = list(P0_ENRICHMENTS[request.enrichment_id])
        evidence_ids = [item.evidence_id for item in request.dossier.evidence]
        assertion = {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": fields},
                "value": {"type": "string"},
                "evidence_ids": {
                    "type": "array", "items": {"type": "string", "enum": evidence_ids},
                    "minItems": 1,
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "visibility": {
                    "type": "string", "enum": [item.value for item in Visibility],
                },
            },
            "required": ["field", "value", "evidence_ids", "confidence", "visibility"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "assertions": {"type": "array", "items": assertion},
                "unknowns": {
                    "type": "array", "items": {"type": "string", "enum": fields},
                },
            },
            "required": ["assertions", "unknowns"],
            "additionalProperties": False,
        }

    def _body(self, request: ExperimentInput) -> dict[str, Any]:
        body = {
            "model": request.requested_model_id,
            "input": self._prompt(request),
            "max_output_tokens": self._max_output_tokens(
                request.requested_model_id, request,
            ),
            "store": True,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "company_enrichment",
                    "strict": True,
                    "schema": self._schema(request),
                }
            },
        }
        if request.requested_model_id == "gpt-5-nano":
            body["reasoning"] = {"effort": "minimal"}
        if self._is_field_keyed(request) and not request.requested_model_id.startswith(
            _REASONING_MODEL_PREFIXES
        ):
            body["temperature"] = _FIELD_KEYED_TEMPERATURE
        return body

    def _request_digest(self, request: ExperimentInput) -> str:
        material = {
            "company_id": request.company_id,
            "enrichment_id": request.enrichment_id,
            "provider_body": self._body(request),
        }
        metadata = self._override_metadata(request)
        if metadata:
            material["prompt_metadata"] = metadata
        return sha256(canonical_json(material).encode("utf-8")).hexdigest()

    def _execute_sync(self, request: ExperimentInput) -> ModelExecution:
        digest = self._request_digest(request)
        path = self._root / "sync" / f"{digest}.json"
        state = _read_json(path)
        attempt_history: list[Mapping[str, Any]] = []
        if state is not None and state.get("status") == "terminal":
            attempt_history = list(state.get("attempt_history", ()))
            prior = dict(state)
            prior.pop("attempt_history", None)
            attempt_history.append(prior)
            state = None
        if state is not None and state.get("status") == "completed":
            return self._execution_from_payload(state["execution"])
        if state is not None and state.get("status") == "received":
            response = state["provider_response"]
            latency_ms = int(state["latency_ms"])
        else:
            started = self._monotonic()
            attempt_index = len(attempt_history)
            idempotency_key = (
                f"company-enrichment-{digest}-attempt-{attempt_index}"
            )
            try:
                response = self._sdk.responses.create(
                    **self._body(request),
                    extra_headers={"Idempotency-Key": idempotency_key},
                )
            except Exception as error:
                raise self._provider_error("Responses create", error) from None
            latency_ms = max(0, round((self._monotonic() - started) * 1000))
            state = {
                "attempt_history": attempt_history,
                "attempt_index": attempt_index,
                "company_id": request.company_id,
                "enrichment_id": request.enrichment_id,
                "idempotency_key": idempotency_key,
                "latency_ms": latency_ms,
                "provider_response": _response_payload(response),
                "request_sha256": digest,
                "status": "received",
                **self._request_metadata(request),
            }
            _atomic_json(path, state)
        try:
            execution = self._execution_from_response(
                request, response, ExecutionTrack.SYNCHRONOUS, latency_ms,
            )
        except ValueError:
            _atomic_json(path, {
                **state,
                "provider_status": "invalid_output",
                "status": "terminal",
            })
            raise
        _atomic_json(path, {
            **state,
            "execution": self._execution_payload(execution),
            "status": "completed",
        })
        return execution

    @staticmethod
    def _provider_error(operation: str, error: Exception) -> OpenAIProviderError:
        # The provider's message may echo an Authorization header or key. Error
        # type is enough for retry classification and cannot disclose the value.
        return OpenAIProviderError(
            f"OpenAI {operation} failed ({type(error).__name__})"
        )

    def _execution_from_response(
        self,
        request: ExperimentInput,
        response: Any,
        track: ExecutionTrack,
        latency_ms: int,
    ) -> ModelExecution:
        text = _attribute(response, "output_text")
        if not isinstance(text, str) or not text.strip():
            text = self._output_text_from_body(response)
        try:
            output = json.loads(text)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("provider returned invalid structured JSON") from error
        assertions, unknowns = self._validated_output(request, output)
        usage = _attribute(response, "usage")
        input_tokens = int(_attribute(usage, "input_tokens", 0) or 0)
        output_tokens = int(_attribute(usage, "output_tokens", 0) or 0)
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("provider usage tokens must be non-negative")
        details = _attribute(usage, "input_tokens_details", {}) or {}
        cached_tokens = int(_attribute(details, "cached_tokens", 0) or 0)
        if cached_tokens < 0 or cached_tokens > input_tokens:
            raise ValueError("provider cached usage tokens are invalid")
        input_rate, cached_rate, output_rate = self._price(
            request.requested_model_id, track,
        )
        cost = (
            Decimal(input_tokens - cached_tokens) * input_rate / _MILLION
            + Decimal(cached_tokens) * cached_rate / _MILLION
            + Decimal(output_tokens) * output_rate / _MILLION
        )
        resolved = _attribute(response, "model")
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError("provider response must resolve the model ID")
        return ModelExecution(
            request.company_id, assertions, unknowns, resolved, latency_ms, str(cost),
        )

    @staticmethod
    def _output_text_from_body(response: Any) -> str:
        for output in _attribute(response, "output", ()) or ():
            if _attribute(output, "type") != "message":
                continue
            for content in _attribute(output, "content", ()) or ():
                if _attribute(content, "type") == "output_text":
                    text = _attribute(content, "text")
                    if isinstance(text, str):
                        return text
        raise ValueError("provider response does not contain output text")

    @staticmethod
    def _validated_output(
        request: ExperimentInput, output: Any,
    ) -> tuple[tuple[FieldAssertion, ...], tuple[str, ...]]:
        if request.output_contract is not None:
            if request.enrichment_id != "icp-persona-analysis":
                return OpenAIModelClient._validated_field_keyed_output(request, output)
            return OpenAIModelClient._validated_icp_output(request, output)

        if not isinstance(output, Mapping):
            raise ValueError("structured output must be an object")
        assertion_values = output.get("assertions")
        unknown_values = output.get("unknowns")
        if not isinstance(assertion_values, list) or not isinstance(unknown_values, list):
            raise ValueError("structured output requires assertions and unknowns arrays")
        allowed_fields = set(P0_ENRICHMENTS[request.enrichment_id])
        allowed_evidence = {item.evidence_id for item in request.dossier.evidence}
        assertions = []
        for value in assertion_values:
            if not isinstance(value, Mapping):
                raise ValueError("assertion output must be an object")
            field = value.get("field")
            evidence_ids = tuple(value.get("evidence_ids", ()))
            if field not in allowed_fields:
                raise ValueError("assertion field is outside enrichment scope")
            if not evidence_ids or not set(evidence_ids).issubset(allowed_evidence):
                raise ValueError("assertion cites Evidence outside the dossier")
            try:
                visibility = Visibility(value.get("visibility"))
                confidence = float(value.get("confidence"))
            except (TypeError, ValueError) as error:
                raise ValueError("assertion metadata is invalid") from error
            assertions.append(FieldAssertion(
                field, value.get("value"), evidence_ids, confidence, visibility,
            ))
        unknowns = tuple(unknown_values)
        if any(not isinstance(item, str) or item not in allowed_fields for item in unknowns):
            raise ValueError("unknown field is outside enrichment scope")
        asserted_fields = [item.field for item in assertions]
        if set(asserted_fields) | set(unknowns) != allowed_fields:
            raise ValueError("structured output must cover every requested field")
        return tuple(assertions), unknowns

    @staticmethod
    def _validated_icp_output(
        request: ExperimentInput, output: Any,
    ) -> tuple[tuple[FieldAssertion, ...], tuple[str, ...]]:
        if request.enrichment_id != "icp-persona-analysis":
            raise ValueError("nested output contract requires ICP/persona enrichment")
        if not isinstance(output, dict):
            raise ValueError("structured ICP output must be an object")
        parsed = parse_icp_output(
            output, {item.evidence_id for item in request.dossier.evidence},
        )
        icp_ids: list[str] = []
        for component in (
            parsed.primary_icp, *parsed.secondary_icps, *parsed.outcomes,
        ):
            icp_ids.extend(component.evidence_ids)
        persona_ids: list[str] = []
        for component in parsed.observed_personas:
            persona_ids.extend(component.evidence_ids)
        for component in parsed.inferred_personas:
            persona_ids.extend(component.based_on_evidence_ids)
        icp_value = {
            "primary_icp": output["primary_icp"],
            "secondary_icps": output.get("secondary_icps", []),
            "outcomes": output.get("outcomes", []),
        }
        personas_value = {
            "observed_personas": output.get("observed_personas", []),
            "inferred_personas": output.get("inferred_personas", []),
        }
        return (
            (
                FieldAssertion(
                    "icp", icp_value, tuple(dict.fromkeys(icp_ids)), 1.0,
                    Visibility.MESSAGE_SAFE,
                ),
                FieldAssertion(
                    "personas", personas_value,
                    tuple(dict.fromkeys(persona_ids)), 1.0,
                    Visibility.MESSAGE_SAFE,
                ),
            ),
            (),
        )

    @staticmethod
    def _is_empty_collection(value: Any) -> bool:
        """True for ``[]`` or an object whose list-valued members are all empty."""
        if isinstance(value, list):
            return not value
        if isinstance(value, Mapping):
            members = [item for item in value.values() if isinstance(item, list)]
            return bool(members) and all(not item for item in members)
        return False

    @staticmethod
    def _collect_evidence_ids(value: Any, allowed: set[str]) -> list[str]:
        """Collect every nested ``evidence_ids`` array, each non-empty and retained."""
        found: list[str] = []
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key != "evidence_ids":
                    found.extend(OpenAIModelClient._collect_evidence_ids(item, allowed))
                    continue
                if not isinstance(item, list) or not item:
                    raise ValueError("evidence_ids must be a non-empty array")
                if any(not isinstance(evidence_id, str) for evidence_id in item):
                    raise ValueError("evidence_ids must contain text IDs")
                if not set(item).issubset(allowed):
                    raise ValueError("assertion cites Evidence outside the dossier")
                found.extend(item)
        elif isinstance(value, list):
            for item in value:
                found.extend(OpenAIModelClient._collect_evidence_ids(item, allowed))
        return found

    @staticmethod
    def _validated_field_keyed_output(
        request: ExperimentInput, output: Any,
    ) -> tuple[tuple[FieldAssertion, ...], tuple[str, ...]]:
        """Validate a nested contract keyed by the enrichment's P0 fields.

        Top-level keys must equal the enrichment's fields, plus an optional
        ``unknowns`` list naming fields within scope. Every nested
        ``evidence_ids`` array must be non-empty and cite retained Evidence.
        Each field becomes one message-safe FieldAssertion whose evidence is
        the union of the arrays nested under it; a field with no citation
        must be declared unknown.
        """
        if not isinstance(output, dict):
            raise ValueError("structured field-keyed output must be an object")
        fields = P0_ENRICHMENTS[request.enrichment_id]
        if set(output) - {"unknowns"} != set(fields):
            raise ValueError("field-keyed output must contain exactly the requested fields")
        unknown_values = output.get("unknowns", [])
        if not isinstance(unknown_values, list) or any(
            not isinstance(item, str) or item not in fields for item in unknown_values
        ):
            raise ValueError("unknown field is outside enrichment scope")
        unknowns = tuple(dict.fromkeys(unknown_values))
        allowed_evidence = {item.evidence_id for item in request.dossier.evidence}
        assertions = []
        for field in fields:
            evidence_ids = tuple(dict.fromkeys(
                OpenAIModelClient._collect_evidence_ids(output[field], allowed_evidence)
            ))
            if not evidence_ids and field not in unknowns:
                if OpenAIModelClient._is_empty_collection(output[field]):
                    # An explicitly empty collection is the model saying "nothing
                    # supported"; treat it as the unknown it is rather than
                    # failing the whole case over a missing declaration.
                    unknowns = (*unknowns, field)
                else:
                    raise ValueError(f"field {field} cites no Evidence and is not unknown")
            assertions.append(FieldAssertion(
                field, output[field], evidence_ids, 1.0, Visibility.MESSAGE_SAFE,
            ))
        return tuple(assertions), unknowns

    @staticmethod
    def _execution_payload(value: ModelExecution) -> dict[str, Any]:
        return {
            "actual_cost_usd": value.actual_cost_usd,
            "assertions": [
                {
                    "confidence": item.confidence,
                    "evidence_ids": list(item.evidence_ids),
                    "field": item.field,
                    "value": item.value,
                    "visibility": item.visibility.value,
                }
                for item in value.assertions
            ],
            "company_id": value.company_id,
            "latency_ms": value.latency_ms,
            "resolved_model_id": value.resolved_model_id,
            "unknowns": list(value.unknowns),
        }

    @staticmethod
    def _execution_from_payload(value: Mapping[str, Any]) -> ModelExecution:
        return ModelExecution(
            str(value["company_id"]),
            tuple(FieldAssertion(
                item["field"], item["value"], tuple(item["evidence_ids"]),
                float(item["confidence"]), Visibility(item["visibility"]),
            ) for item in value["assertions"]),
            tuple(value["unknowns"]),
            value["resolved_model_id"],
            int(value["latency_ms"]),
            str(value["actual_cost_usd"]),
        )

    def _execute_batch(
        self, requests: tuple[ExperimentInput, ...],
    ) -> tuple[ModelExecution, ...]:
        path = self._batch_path(requests)
        digest = path.stem
        state = _read_json(path)
        started = self._monotonic()
        attempt_history: list[Mapping[str, Any]] = []
        if state is not None and state.get("status") == "terminal":
            attempt_history = list(state.get("attempt_history", ()))
            prior = dict(state)
            prior.pop("attempt_history", None)
            attempt_history.append(prior)
            state = None
        if state is not None and state.get("status") == "completed":
            return tuple(
                self._execution_from_payload(item)
                for item in state["executions"]
            )
        if state is None:
            lines = tuple(
                {
                    "body": self._body(request),
                    "custom_id": request.company_id,
                    "method": "POST",
                    "url": "/v1/responses",
                }
                for request in requests
            )
            material = "".join(canonical_json(line) + "\n" for line in lines)
            upload_stream = io.BytesIO(material.encode("utf-8"))
            upload_stream.name = f"company-enrichment-{digest}.jsonl"
            try:
                uploaded = self._sdk.files.create(
                    file=upload_stream,
                    purpose="batch",
                )
            except Exception as error:
                raise self._provider_error("Batch input upload", error) from None
            input_file_id = _attribute(uploaded, "id")
            if not isinstance(input_file_id, str) or not input_file_id:
                raise ValueError("Batch input upload did not return a file ID")
            state = {
                "attempt_history": attempt_history,
                "attempt_index": len(attempt_history),
                "input_file_id": input_file_id,
                "request_sha256": digest,
                "status": "uploaded",
                "request_metadata": [
                    self._request_metadata(item) for item in requests
                ],
            }
            _atomic_json(path, state)
        if state.get("status") == "uploaded":
            try:
                job = self._sdk.batches.create(
                    completion_window="24h",
                    endpoint="/v1/responses",
                    input_file_id=state["input_file_id"],
                    metadata={
                        "attempt_index": str(state["attempt_index"]),
                        "request_sha256": digest,
                    },
                    extra_headers={
                        "Idempotency-Key": (
                            f"company-enrichment-batch-{digest}"
                            f"-attempt-{state['attempt_index']}"
                        ),
                    },
                )
            except Exception:
                raise OpenAIBatchPending(
                    "OpenAI Batch creation outcome is unknown; resume this command"
                ) from None
            batch_id = _attribute(job, "id")
            if not isinstance(batch_id, str) or not batch_id:
                raise OpenAIBatchPending(
                    "OpenAI Batch create returned no job ID; resume this command"
                )
            state = dict(
                state, batch_id=batch_id,
                provider_status=_attribute(job, "status", "validating"),
                status="pending",
            )
            _atomic_json(path, state)
        if state.get("status") == "received":
            output_file_id = state["output_file_id"]
            safe_rows = state["provider_output"]
        else:
            batch_id = state.get("batch_id")
            while True:
                try:
                    job = self._sdk.batches.retrieve(batch_id)
                except Exception:
                    raise OpenAIBatchPending(
                        f"OpenAI Batch {batch_id} retrieval failed; resume this command"
                    ) from None
                status = _attribute(job, "status")
                if status == "completed":
                    break
                if status in {"failed", "expired", "cancelled"}:
                    state = {
                        **state,
                        "error_file_id": _attribute(job, "error_file_id"),
                        "output_file_id": _attribute(job, "output_file_id"),
                        "provider_status": status,
                        "status": "terminal",
                    }
                    _atomic_json(path, state)
                    raise OpenAIProviderError(
                        f"OpenAI Batch ended in terminal status {status}"
                    )
                state = dict(state, provider_status=status, status="pending")
                _atomic_json(path, state)
                if self._monotonic() - started >= self._max_poll:
                    raise OpenAIBatchPending(
                        f"OpenAI Batch {batch_id} remains pending; resume this command"
                    )
                self._sleep(self._poll_interval)
            output_file_id = _attribute(job, "output_file_id")
            if not isinstance(output_file_id, str) or not output_file_id:
                raise ValueError("completed Batch does not include an output file")
            try:
                content = self._sdk.files.content(output_file_id)
            except Exception:
                raise OpenAIBatchPending(
                    f"OpenAI Batch {batch_id} output is not available; resume this command"
                ) from None
            raw = self._file_content_text(content)
            safe_rows = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise ValueError("Batch output row must be an object")
                safe_rows.append(_safe_provider_value(value))
            state = {
                **state,
                "output_file_id": output_file_id,
                "provider_output": safe_rows,
                "provider_status": "completed",
                "status": "received",
            }
            _atomic_json(path, state)
        expected = {request.company_id for request in requests}
        try:
            by_id = self._validated_batch_bodies(safe_rows, expected)
        except (OpenAIProviderError, ValueError):
            _atomic_json(path, {
                **state,
                "provider_status": "invalid_output",
                "status": "terminal",
            })
            raise
        latency_ms = max(0, round((self._monotonic() - started) * 1000))
        try:
            executions = tuple(
                self._execution_from_response(
                    request, by_id[request.company_id], ExecutionTrack.BATCH,
                    latency_ms,
                )
                for request in requests
            )
        except ValueError:
            _atomic_json(path, {
                **state,
                "provider_status": "invalid_output",
                "status": "terminal",
            })
            raise
        _atomic_json(path, {
            **state,
            "executions": [
                self._execution_payload(item) for item in executions
            ],
            "provider_status": "completed",
            "status": "completed",
        })
        return executions

    @staticmethod
    def _validated_batch_bodies(
        safe_rows: Sequence[Mapping[str, Any]], expected: set[str],
    ) -> dict[str, Mapping[str, Any]]:
        by_id: dict[str, Mapping[str, Any]] = {}
        for value in safe_rows:
            custom_id = value.get("custom_id")
            if not isinstance(custom_id, str) or custom_id in by_id:
                raise ValueError("Batch output custom IDs must be unique text")
            error = value.get("error")
            response = value.get("response")
            if error is not None or not isinstance(response, Mapping):
                raise OpenAIProviderError("OpenAI Batch returned a failed case")
            status_code = int(response.get("status_code", 0) or 0)
            body = response.get("body")
            if status_code < 200 or status_code >= 300 or not isinstance(body, Mapping):
                raise OpenAIProviderError(
                    "OpenAI Batch returned a non-success response"
                )
            by_id[custom_id] = body
        if set(by_id) != expected:
            raise ValueError("Batch output does not match requested company IDs")
        return by_id

    def _batch_path(
        self, requests: tuple[ExperimentInput, ...],
    ) -> Path:
        request_values = []
        for request in requests:
            value = {
                "body": self._body(request),
                "company_id": request.company_id,
                "enrichment_id": request.enrichment_id,
            }
            metadata = self._override_metadata(request)
            if metadata:
                value["prompt_metadata"] = metadata
            request_values.append(value)
        material = canonical_json({"requests": request_values})
        digest = sha256(material.encode("utf-8")).hexdigest()
        return self._root / "batch" / f"{digest}.json"

    @staticmethod
    def _file_content_text(content: Any) -> str:
        text = _attribute(content, "text")
        if isinstance(text, str):
            return text
        raw = _attribute(content, "content")
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        read = getattr(content, "read", None)
        if callable(read):
            value = read()
            if isinstance(value, bytes):
                return value.decode("utf-8")
            if isinstance(value, str):
                return value
        raise ValueError("Batch output file does not contain text")


def build_openai_model_client(*, artifact_root: Path) -> OpenAIModelClient:
    """Build from the key injected into this child process by `lg run`."""
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "OpenAI SDK is required; install requirements.txt"
        ) from error
    return OpenAIModelClient(artifact_root=artifact_root, sdk_client=OpenAI())
