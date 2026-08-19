"""Parallel Task API client with hard spend safeguards.

``ParallelTaskClient`` wraps the Parallel Task API (``POST
https://api.parallel.ai/v1/tasks/runs`` plus ``GET
/v1/tasks/runs/{run_id}/result``, verified against https://docs.parallel.ai
on 2026-08-19). Task runs are orders of magnitude more expensive than search
queries (``lite`` $0.005, ``base`` $0.01, ``core`` $0.025 per run; ``pro``
$0.10 and the ``ultra`` tiers $0.30-$2.40), so this client enforces:

1. a processor allowlist - only ``lite`` and ``base`` (the second tier)
   construct; ``core`` and above raise ``ValueError`` with no override flag;
2. a hard budget ceiling - cumulative spend is charged when a run is
   submitted, and a submit that would exceed the ceiling raises
   ``BudgetFailure`` before any HTTP request is made;
3. no auto-retry on submit - an ambiguous transport failure during submit
   raises ``TerminalFailure`` (a blind retry could double-spend); result
   retrieval is a free idempotent GET and stays ``RetryableFailure``;
4. key discipline - ``PARALLEL_API_KEY`` is read only inside
   ``build_parallel_task`` and never logged or persisted.

Budget is charged at submit and never refunded, which over-counts runs the
API ultimately fails for free; that bias is deliberate - the ceiling bounds
worst-case spend, not the invoice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from ..providers import (
    AuthenticationFailure,
    BudgetFailure,
    ContractFailure,
    RetryableFailure,
    SourceObservation,
    TerminalFailure,
)

PARALLEL_TASK_BASE_URL = "https://api.parallel.ai"
ALLOWED_TASK_PROCESSORS = {"lite": "0.005", "base": "0.01"}
DEFAULT_TASK_PROCESSOR = "base"
DEFAULT_TASK_BUDGET_USD = "1.00"
DEFAULT_RESULT_TIMEOUT_SECONDS = 600
_ACTIVE_STATUSES = frozenset({"queued", "action_required", "running", "cancelling"})

HttpPost = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]
HttpGet = Callable[[str, dict[str, str]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class TaskRun:
    run_id: str
    status: str
    processor: str
    cost_usd: str


@dataclass(frozen=True, slots=True)
class TaskResult:
    run_id: str
    status: str
    output_type: str
    content: Any
    citations: tuple[SourceObservation, ...]


def _citation_observations(basis: Any) -> tuple[SourceObservation, ...]:
    observations: list[SourceObservation] = []
    seen: set[str] = set()
    if not isinstance(basis, list):
        return ()
    for entry in basis:
        if not isinstance(entry, dict):
            continue
        for citation in entry.get("citations") or ():
            if not isinstance(citation, dict):
                continue
            url = citation.get("url")
            if not isinstance(url, str) or url in seen:
                continue
            excerpts = citation.get("excerpts")
            snippet = " ".join(
                part for part in excerpts if isinstance(part, str)
            ) if isinstance(excerpts, list) else ""
            title = citation.get("title")
            text = " ".join(f"{title or ''} {snippet}".split()) or url
            try:
                observations.append(SourceObservation(url, text[:2000]))
            except ValueError:
                continue
            seen.add(url)
    return tuple(observations)


class ParallelTaskClient:
    """Budget-guarded client for the Parallel Task API."""

    def __init__(
        self,
        http_post: HttpPost,
        http_get: HttpGet,
        api_key: str,
        *,
        processor: str = DEFAULT_TASK_PROCESSOR,
        budget_usd: str = DEFAULT_TASK_BUDGET_USD,
        base_url: str = PARALLEL_TASK_BASE_URL,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise AuthenticationFailure("PARALLEL_API_KEY is not configured")
        if processor not in ALLOWED_TASK_PROCESSORS:
            raise ValueError(
                f"processor must be one of {sorted(ALLOWED_TASK_PROCESSORS)}; "
                f"tiers above base (core/pro/ultra*) are not allowed"
            )
        try:
            ceiling = Decimal(budget_usd)
        except (InvalidOperation, TypeError):
            raise ValueError("budget_usd must be a decimal string") from None
        if ceiling <= 0:
            raise ValueError("budget_usd must be positive")
        self._http_post = http_post
        self._http_get = http_get
        self._api_key = api_key
        self.processor = processor
        self.cost_per_run_usd = ALLOWED_TASK_PROCESSORS[processor]
        self._budget = ceiling
        self._spent = Decimal("0")
        self._base_url = base_url.rstrip("/")

    @property
    def spent_usd(self) -> str:
        return str(self._spent)

    @property
    def remaining_usd(self) -> str:
        return str(self._budget - self._spent)

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "Content-Type": "application/json"}

    def create_run(
        self,
        task_input: str | dict[str, Any],
        *,
        output_schema: dict[str, Any] | None = None,
        source_policy: dict[str, Any] | None = None,
    ) -> TaskRun:
        """Submit one task run; budget is charged before the request is sent."""
        if isinstance(task_input, str):
            if not task_input.strip():
                raise ValueError("task_input must be non-empty")
        elif not isinstance(task_input, dict):
            raise ValueError("task_input must be a string or an object")
        cost = Decimal(self.cost_per_run_usd)
        if self._spent + cost > self._budget:
            raise BudgetFailure(
                f"parallel task budget exhausted: spent {self._spent} of "
                f"{self._budget} USD; run costs {cost}"
            )
        payload: dict[str, Any] = {"processor": self.processor, "input": task_input}
        if output_schema is not None:
            payload["task_spec"] = {
                "output_schema": {"type": "json", "json_schema": output_schema},
            }
        if source_policy is not None:
            payload["source_policy"] = source_policy
        self._spent += cost
        try:
            response = self._http_post(
                f"{self._base_url}/v1/tasks/runs", payload, self._headers(),
            )
        except (AuthenticationFailure, ContractFailure, BudgetFailure):
            raise
        except RetryableFailure as error:
            # A blind resubmit could double-spend; surface as terminal so the
            # caller decides, with budget already charged for the attempt.
            raise TerminalFailure(f"parallel task submit failed: {error}") from None
        except Exception as error:  # transport failures never carry the key
            raise TerminalFailure(
                f"parallel task submit failed: {type(error).__name__}"
            ) from None
        if not isinstance(response, dict):
            raise ContractFailure("parallel task run returned a non-object response")
        run_id = response.get("run_id")
        status = response.get("status")
        if not isinstance(run_id, str) or not run_id or not isinstance(status, str):
            raise ContractFailure("parallel task run response is missing run_id/status")
        return TaskRun(run_id, status, self.processor, self.cost_per_run_usd)

    def get_result(
        self, run_id: str, *, timeout_seconds: int = DEFAULT_RESULT_TIMEOUT_SECONDS,
    ) -> TaskResult:
        """Fetch a run result; free idempotent GET, safe to retry."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be non-empty")
        url = f"{self._base_url}/v1/tasks/runs/{run_id}/result?timeout={int(timeout_seconds)}"
        response = self._http_get(url, self._headers())
        if not isinstance(response, dict):
            raise ContractFailure("parallel task result is not an object")
        run = response.get("run")
        output = response.get("output")
        if not isinstance(run, dict) or not isinstance(output, dict):
            raise ContractFailure("parallel task result is missing run/output")
        status = str(run.get("status") or "")
        if status in _ACTIVE_STATUSES:
            raise RetryableFailure(f"parallel task run {run_id} is still {status}")
        if status != "completed":
            raise TerminalFailure(f"parallel task run {run_id} ended {status}")
        return TaskResult(
            run_id=run_id,
            status=status,
            output_type=str(output.get("type") or ""),
            content=output.get("content"),
            citations=_citation_observations(output.get("basis")),
        )


def urllib_task_transport(
    timeout_seconds: float = 630,
) -> tuple[HttpPost, HttpGet]:
    def _decode(raw: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContractFailure("parallel returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise ContractFailure("parallel returned a non-object response")
        return decoded

    def _open(http_request: urllib_request.Request) -> dict[str, Any]:
        try:
            with urllib_request.urlopen(http_request, timeout=timeout_seconds) as response:
                return _decode(response.read())
        except urllib_error.HTTPError as error:
            if error.code in {401, 403}:
                raise AuthenticationFailure("parallel rejected the API key") from None
            if error.code == 408:
                raise RetryableFailure("parallel task run is still active") from None
            if error.code == 422:
                raise ContractFailure("parallel rejected the request payload") from None
            raise RetryableFailure(f"parallel HTTP {error.code}") from None
        except (urllib_error.URLError, TimeoutError, OSError) as error:
            raise RetryableFailure(
                f"parallel transport failure: {type(error).__name__}"
            ) from None

    def post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return _open(urllib_request.Request(url, data=body, headers=headers, method="POST"))

    def get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        return _open(urllib_request.Request(url, headers=headers, method="GET"))

    return post, get


def build_parallel_task(
    *,
    processor: str = DEFAULT_TASK_PROCESSOR,
    budget_usd: str = DEFAULT_TASK_BUDGET_USD,
    http_post: HttpPost | None = None,
    http_get: HttpGet | None = None,
) -> ParallelTaskClient:
    """Build a task client from ``PARALLEL_API_KEY`` (injected via ``lg run``)."""
    api_key = os.environ.get("PARALLEL_API_KEY", "").strip()
    if not api_key:
        raise AuthenticationFailure("PARALLEL_API_KEY is not configured")
    if http_post is None or http_get is None:
        default_post, default_get = urllib_task_transport()
        http_post = http_post or default_post
        http_get = http_get or default_get
    return ParallelTaskClient(
        http_post, http_get, api_key, processor=processor, budget_usd=budget_usd,
    )
