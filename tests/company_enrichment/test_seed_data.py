from pathlib import Path

from scripts.company_enrichment.seed_data import (
    deduplicate_ai_ark_seed,
    load_ai_ark_seed,
    missing_seed_fields,
)


def test_ai_ark_seed_normalizes_company_fields_and_discards_contacts(tmp_path: Path) -> None:
    path = tmp_path / "seed.csv"
    path.write_text(
        "Company Name,Headcount,Employee Size,Industry,Industry Tags,"
        "Product and Services,Description,SEO Description,Website,LinkedIn,"
        "Company Email,Primary Company Phone,Founding Year,Annual Revenue,"
        "Total Funding,Last Funding Type,Last Funding Amount,Last Funding Date,"
        "Technologies\n"
        "Acme,42,11-50,Software,\"B2B, SaaS\",Workflow automation,"
        "Automation for finance teams,Finance automation,"
        "https://www.acme.example/about,https://linkedin.com/company/acme,"
        "hello@acme.example,+1-555-0100,2018,1000000-4999999,"
        "5000000,SERIES_A,5000000,2025-01-15,\"Python, AWS\"\n",
        encoding="utf-8",
    )

    seed = load_ai_ark_seed(path)[0]

    assert seed.company_name == "Acme"
    assert seed.domain == "acme.example"
    assert seed.linkedin_company_url == "https://linkedin.com/company/acme"
    assert seed.headcount == 42
    assert seed.employee_size == "11-50"
    assert seed.products_services == "Workflow automation"
    assert seed.annual_revenue_band == "1000000-4999999"
    assert seed.technologies == ("Python", "AWS")
    assert seed.provenance == "ai_ark_seed"
    assert not hasattr(seed, "company_email")
    assert not hasattr(seed, "company_phone")


def test_ai_ark_seed_turns_placeholder_and_blank_values_into_gaps(tmp_path: Path) -> None:
    path = tmp_path / "seed.csv"
    path.write_text(
        "Company Name,Headcount,Employee Size,Industry,Product and Services,"
        "Description,Website,LinkedIn,Founding Year,Annual Revenue,Technologies\n"
        "Sparse,0,null+,,,Sparse description,,"
        "https://linkedin.com/company/sparse,,,\n",
        encoding="utf-8",
    )

    seed = load_ai_ark_seed(path)[0]

    assert seed.employee_size is None
    assert seed.domain is None
    assert seed.products_services is None
    assert seed.technologies == ()
    assert missing_seed_fields(seed) == (
        "domain",
        "industry",
        "products_services",
        "target_customer",
    )


def test_ai_ark_seed_rejects_duplicate_domains(tmp_path: Path) -> None:
    path = tmp_path / "seed.csv"
    path.write_text(
        "Company Name,Website,LinkedIn\n"
        "Acme,acme.example,https://linkedin.com/company/acme\n"
        "Acme Two,www.acme.example,https://linkedin.com/company/acme-two\n",
        encoding="utf-8",
    )

    try:
        load_ai_ark_seed(path)
    except ValueError as error:
        assert "duplicate domain" in str(error)
    else:
        raise AssertionError("duplicate domains must be rejected")

    records = load_ai_ark_seed(path, reject_duplicate_domains=False)
    assert len(records) == 2
    assert len(deduplicate_ai_ark_seed(records)) == 1


def test_deduplication_keeps_the_most_complete_row() -> None:
    from scripts.company_enrichment.seed_data import CompanySeed

    sparse = CompanySeed(
        company_name="Acme",
        domain="acme.example",
        linkedin_company_url=None,
        headcount=None,
        employee_size=None,
        industry=None,
        industry_tags=(),
        products_services=None,
        description=None,
        seo_description=None,
        company_type=None,
        country=None,
        state=None,
        city=None,
        founding_year=None,
        annual_revenue_band=None,
        total_funding=None,
        last_funding_type=None,
        last_funding_amount=None,
        last_funding_date=None,
        technologies=(),
    )
    complete = CompanySeed(
        company_name="Acme Inc.",
        domain="acme.example",
        linkedin_company_url="https://linkedin.com/company/acme",
        headcount=42,
        employee_size="11-50",
        industry="Software",
        industry_tags=("B2B",),
        products_services="Automation",
        description="Automation for finance teams",
        seo_description=None,
        company_type="PRIVATELY_HELD",
        country="United States",
        state="New York",
        city="New York",
        founding_year=2018,
        annual_revenue_band="1000000-4999999",
        total_funding=None,
        last_funding_type=None,
        last_funding_amount=None,
        last_funding_date=None,
        technologies=("Python",),
    )

    assert deduplicate_ai_ark_seed((sparse, complete)) == (complete,)
