"""Contracts for the resumable-autoresearch domain documentation."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TERMS = {
    "Research Flow",
    "Search Flow",
    "Site Extraction Flow",
    "Source Adapter",
    "Experiment",
    "Evidence",
    "Approval",
}


def _context_terms(text: str) -> set[str]:
    return set(re.findall(r"^\*\*([^*]+)\*\*:$", text, flags=re.MULTILINE))


def _definition(text: str, term: str) -> str:
    match = re.search(
        rf"^\*\*{re.escape(term)}\*\*:\s*\n(.*?)(?=^\*\*|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"{term} must have a definition"
    return match.group(1)


def test_context_defines_only_the_canonical_resumable_autoresearch_terms():
    context = ROOT / "CONTEXT.md"
    assert context.is_file(), "root CONTEXT.md must define the canonical domain language"

    text = context.read_text(encoding="utf-8")
    assert _context_terms(text) == CANONICAL_TERMS


def test_approval_requires_programmed_validation_before_explicit_human_review():
    text = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    approval = _definition(text, "Approval")

    assert re.search(r"programmed\s+ground-truth\s+validation", approval, flags=re.IGNORECASE)
    assert re.search(r">=\s*90%", approval)
    assert re.search(
        r"programmed\s+ground-truth\s+validation.*followed\s+by\s+explicit\s+human\s+review",
        approval,
        flags=re.IGNORECASE | re.DOTALL,
    )


def test_adr_requires_fresh_bounded_request_envelopes_without_raw_role_context():
    text = (ROOT / "docs" / "domain" / "adr" / "0003-resumable-autoresearch-orchestration.md").read_text(
        encoding="utf-8"
    )

    assert re.search(r"fresh,?\s+(?:compact|bounded)\s+request envelope.*each role", text, flags=re.IGNORECASE)
    assert re.search(r"no inherited or raw role context", text, flags=re.IGNORECASE)


def test_adr_requires_named_clis_to_be_thin_compatibility_shims():
    text = (ROOT / "docs" / "domain" / "adr" / "0003-resumable-autoresearch-orchestration.md").read_text(
        encoding="utf-8"
    )

    assert "autoresearch_agent.py" in text
    assert "autocontext_runner.py" in text
    assert re.search(r"thin compatibility CLI(?:s)?/shims?", text, flags=re.IGNORECASE)


def test_orchestration_adr_records_the_interface_and_decision_tradeoffs():
    adr = ROOT / "docs" / "domain" / "adr" / "0003-resumable-autoresearch-orchestration.md"
    assert adr.is_file(), "the resumable orchestration ADR must exist"

    text = adr.read_text(encoding="utf-8")
    for section in ("## Context", "## Considered Options", "## Decision", "## Consequences"):
        assert section in text
    assert "AutoresearchOrchestrator.run(request: RunRequest) -> RunSummary" in text
    assert "persisted state machine" in text.lower()
    assert "append-only journal" in text.lower()
