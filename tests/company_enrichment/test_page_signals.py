import json

from scripts.company_enrichment.page_signals import (
    FetchOutcome,
    check_domain,
    check_signal,
    classify_path,
    run_corpus,
)


FINGERPRINT_404 = FetchOutcome(
    requested_url="https://acme.com/lg-x", final_url="https://acme.com/lg-x",
    status=404,
)
FINGERPRINT_SOFT = FetchOutcome(
    requested_url="https://acme.com/lg-x",
    final_url="https://acme.com/lg-x",
    status=200, title="Acme - Page Not Found",
)


def _outcome(url, status=200, final_url=None, title="Careers at Acme",
             error=None):
    return FetchOutcome(
        requested_url=url, final_url=final_url or url, status=status,
        title=title, error=error,
    )


def test_renders_is_present():
    state, reason, ats = classify_path(
        _outcome("https://acme.com/careers"),
        domain="acme.com", fingerprint=FINGERPRINT_404,
    )
    assert (state, reason, ats) == ("present", "renders", None)


def test_hard_404_is_absent():
    state, reason, _ats = classify_path(
        _outcome("https://acme.com/careers", status=404),
        domain="acme.com", fingerprint=FINGERPRINT_404,
    )
    assert state == "absent"
    assert reason == "status:404"


def test_soft_404_title_match_is_absent():
    state, reason, _ats = classify_path(
        _outcome("https://acme.com/careers", title="Acme - Page Not Found"),
        domain="acme.com", fingerprint=FINGERPRINT_SOFT,
    )
    assert state == "absent"
    assert reason == "soft_404_title_match"


def test_redirect_to_homepage_is_absent():
    state, reason, _ats = classify_path(
        _outcome(
            "https://acme.com/careers", final_url="https://www.acme.com/",
        ),
        domain="acme.com", fingerprint=FINGERPRINT_404,
    )
    assert state == "absent"
    assert reason == "redirected_to_homepage"


def test_ats_redirect_is_present_with_host():
    state, reason, ats = classify_path(
        _outcome(
            "https://acme.com/careers",
            final_url="https://boards.greenhouse.io/acme",
        ),
        domain="acme.com", fingerprint=FINGERPRINT_404,
    )
    assert (state, reason, ats) == ("present", "ats_redirect", "greenhouse.io")


def test_blocked_status_is_unknown():
    state, reason, _ats = classify_path(
        _outcome("https://acme.com/careers", status=403, title=""),
        domain="acme.com", fingerprint=FINGERPRINT_404,
    )
    assert state == "unknown"
    assert reason == "blocked_status:403"


def test_fetch_error_is_unknown():
    state, reason, _ats = classify_path(
        FetchOutcome(requested_url="https://acme.com/careers", error="URLError"),
        domain="acme.com", fingerprint=FINGERPRINT_404,
    )
    assert state == "unknown"
    assert reason == "fetch_error:URLError"


def test_waterfall_stops_at_first_present():
    calls = []

    def fetcher(url):
        calls.append(url)
        if url.endswith("/careers"):
            return _outcome(url, status=404, title="")
        return _outcome(url, title="Jobs at Acme")

    result = check_signal(
        "careers", base_url="https://acme.com", domain="acme.com",
        fingerprint=FINGERPRINT_404, fetcher=fetcher,
    )
    assert result.state == "present"
    assert result.url == "https://acme.com/jobs"
    assert calls == ["https://acme.com/careers", "https://acme.com/jobs"]


def test_all_absent_with_one_blocked_is_unknown():
    def fetcher(url):
        if url.endswith("/jobs"):
            return _outcome(url, status=403, title="")
        return _outcome(url, status=404, title="")

    result = check_signal(
        "careers", base_url="https://acme.com", domain="acme.com",
        fingerprint=FINGERPRINT_404, fetcher=fetcher,
    )
    assert result.state == "unknown"
    assert result.url is None


def test_check_domain_falls_back_to_www_base():
    def fetcher(url):
        if url.startswith("https://acme.com"):
            return FetchOutcome(requested_url=url, error="URLError")
        if "/lg-" in url:
            return _outcome(url, status=404, title="")
        if url.endswith("/blog"):
            return _outcome(url, title="Acme Blog")
        return _outcome(url, status=404, title="")

    record = check_domain("acme.com", fetcher=fetcher)
    assert record["base_url"] == "https://www.acme.com"
    assert record["signals"]["blog"]["state"] == "present"
    assert record["signals"]["careers"]["state"] == "absent"


def test_run_corpus_journals_results(tmp_path):
    def fetcher(url):
        if "/lg-" in url:
            return _outcome(url, status=404, title="")
        if url.endswith("/careers"):
            return _outcome(
                url, final_url="https://jobs.lever.co/acme",
                title="Acme jobs",
            )
        return _outcome(url, status=404, title="")

    summary = run_corpus(
        [("saas-99", "acme.com")], output_dir=tmp_path, fetcher=fetcher,
    )
    assert summary["companies"] == 1
    assert summary["tallies"]["careers"]["present"] == 1
    assert summary["tallies"]["blog"]["absent"] == 1
    lines = (tmp_path / "results.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()
    record = json.loads(lines[0])
    assert record["company_id"] == "saas-99"
    assert record["signals"]["careers"]["ats_host"] == "lever.co"
