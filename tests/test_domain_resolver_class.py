"""
RED test for Issue #11: DomainResolver class.

Problem: domain_resolver.py exposes 8+ module-level functions.
Every caller (series_a_pipeline.py, eval_pipeline.py, pipeline_base.py)
imports individual internals. Callers are coupled to the implementation model.

Solution: Introduce DomainResolver class with three public methods:
  - resolve(company_name, source_url, ...) -> dict
  - dedup(companies, ...) -> list[dict]
  - validate(domain, company_name, source_domain) -> dict

The class wraps the existing free functions so callers can import one name
instead of 3-8 scattered symbols.
"""

import sys
from pathlib import Path

# Make scripts/ importable from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from domain_resolver import DomainResolver


class TestDomainResolverClassInterface:
    """DomainResolver must expose resolve(), dedup(), validate() as public methods."""

    def setup_method(self):
        self.resolver = DomainResolver()

    # -----------------------------------------------------------------
    # validate()
    # -----------------------------------------------------------------

    def test_validate_rejects_news_domain(self):
        result = self.resolver.validate("techcrunch.com", "Acme Corp")
        assert result["valid"] is False, "News domains must be rejected"

    def test_validate_accepts_company_domain(self):
        result = self.resolver.validate("stripe.com", "Stripe")
        assert result["valid"] is True, "Exact name-match company domains must pass"

    def test_validate_returns_dict_with_required_keys(self):
        result = self.resolver.validate("example.com", "Example Inc")
        assert "valid" in result
        assert "reason" in result
        assert "confidence" in result

    # -----------------------------------------------------------------
    # resolve()
    # -----------------------------------------------------------------

    def test_resolve_returns_dict_with_required_keys(self):
        # No API keys in test env — should fall through to not_found gracefully
        result = self.resolver.resolve("Nonexistent Corp XYZ", "https://techcrunch.com/article")
        assert "domain" in result
        assert "tier" in result
        assert "tier_name" in result
        assert "confidence" in result

    def test_resolve_source_is_company_shortcircuits(self):
        # When source URL IS the company site, tier 0 (source_is_company) fires
        result = self.resolver.resolve(
            "Stripe",
            "https://stripe.com/blog/funding-round",
        )
        assert result["tier"] == 0
        assert result["tier_name"] == "source_is_company"
        assert result["domain"] == "stripe.com"

    # -----------------------------------------------------------------
    # dedup()
    # -----------------------------------------------------------------

    def test_dedup_removes_duplicate_companies(self):
        companies = [
            {"company_name": "Acme Inc", "company_domain": "acme.com", "best_score": 5},
            {"company_name": "Acme", "company_domain": "acme.com", "best_score": 3},
        ]
        result = self.resolver.dedup(companies)
        assert len(result) == 1, "Domain-identical companies should be merged to one"

    def test_dedup_preserves_highest_score(self):
        companies = [
            {"company_name": "Acme Inc", "company_domain": "acme.com", "best_score": 5},
            {"company_name": "Acme", "company_domain": "acme.com", "best_score": 3},
        ]
        result = self.resolver.dedup(companies)
        assert result[0]["best_score"] == 5

    def test_dedup_returns_list(self):
        result = self.resolver.dedup([])
        assert isinstance(result, list)

    # -----------------------------------------------------------------
    # Backwards compatibility: free functions still importable
    # -----------------------------------------------------------------

    def test_free_functions_still_importable(self):
        """Existing callers (eval_pipeline.py etc.) must not break."""
        from domain_resolver import (
            validate_domain,
            resolve_domain,
            fuzzy_dedup_companies,
            normalize_domain,
            names_are_similar,
            match_existing_company,
        )
        # All six symbols must still exist (no-op import check)
        assert callable(validate_domain)
        assert callable(resolve_domain)
        assert callable(fuzzy_dedup_companies)
        assert callable(normalize_domain)
        assert callable(names_are_similar)
        assert callable(match_existing_company)
