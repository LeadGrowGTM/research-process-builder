from __future__ import annotations

import pytest

from scripts.company_enrichment.adapters.serper import (
    SERPER_COST_PER_QUERY_USD, SerperSearchClient, build_serper_search, observation_excerpt,
)
from scripts.company_enrichment.providers import (
    AuthenticationFailure, ContractFailure, RetryableFailure, SearchRequest,
)


class RecordingPost:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, payload, headers):
        self.calls.append((url, payload, headers))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_news_mode_posts_to_news_endpoint_and_folds_fields():
    post = RecordingPost({"news": [
        {"title": "AgencyAnalytics Launches X", "link": "https://www.prnewswire.com/a",
         "snippet": "Today announced", "date": "Mar 5, 2024", "source": "PR Newswire"},
        {"title": "no link", "snippet": "dropped"},
        {"title": "bad scheme", "link": "ftp://example.com/x", "snippet": "dropped"},
    ]})
    client = SerperSearchClient(post, "secret-key", mode="news", tbs="qdr:y", num=10)

    result = client.search(SearchRequest("agencyanalytics.com news"))

    url, payload, headers = post.calls[0]
    assert url == "https://google.serper.dev/news"
    assert payload == {"q": "agencyanalytics.com news", "num": 10, "tbs": "qdr:y"}
    assert headers["X-API-KEY"] == "secret-key"
    assert headers["Content-Type"] == "application/json"
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.url == "https://www.prnewswire.com/a"
    assert observation.excerpt == (
        "Title: AgencyAnalytics Launches X\nDate: Mar 5, 2024\nSource: PR Newswire\n"
        "Snippet: Today announced"
    )
    assert client.provider == "serper"
    assert client.cost_per_query_usd == SERPER_COST_PER_QUERY_USD == "0.001"


def test_web_mode_reads_organic_without_tbs():
    post = RecordingPost({"organic": [
        {"title": "AgencyAnalytics vs DashThis", "link": "https://g2.com/compare",
         "snippet": "Compare features"},
    ], "credits": 1})
    client = SerperSearchClient(post, "k", mode="web")

    result = client.search(SearchRequest("AgencyAnalytics vs"))

    url, payload, _ = post.calls[0]
    assert url == "https://google.serper.dev/search"
    assert "tbs" not in payload
    assert result.observations[0].excerpt.startswith("Title: AgencyAnalytics vs DashThis")


def test_for_query_derives_sibling_client_sharing_transport():
    post = RecordingPost({"organic": []})
    client = SerperSearchClient(post, "k", mode="web")

    sibling = client.for_query(mode="news", tbs="qdr:y")
    assert sibling is not client and sibling.mode == "news" and sibling.tbs == "qdr:y"
    assert client.for_query(mode="web", tbs=None) is client
    assert sibling.search(SearchRequest("q")).observations == ()
    assert post.calls[0][0].endswith("/news")


def test_empty_result_key_is_zero_observations_not_failure():
    client = SerperSearchClient(RecordingPost({"searchParameters": {}}), "k", mode="news")
    assert client.search(SearchRequest("q")).observations == ()


@pytest.mark.parametrize("response, error", [
    (AuthenticationFailure("rejected"), AuthenticationFailure),
    (RuntimeError("socket"), RetryableFailure),
    ("not an object", ContractFailure),
    ({"organic": "nope"}, ContractFailure),
])
def test_failures_are_typed(response, error):
    client = SerperSearchClient(RecordingPost(response), "k")
    with pytest.raises(error):
        client.search(SearchRequest("q"))


def test_transport_failure_message_never_carries_key():
    client = SerperSearchClient(RecordingPost(RuntimeError("leak secret-key")), "secret-key")
    with pytest.raises(RetryableFailure) as info:
        client.search(SearchRequest("q"))
    assert "secret-key" not in str(info.value)


def test_client_rejects_bad_configuration():
    with pytest.raises(AuthenticationFailure):
        SerperSearchClient(RecordingPost({}), "")
    with pytest.raises(ValueError):
        SerperSearchClient(RecordingPost({}), "k", mode="images")
    with pytest.raises(ValueError):
        SerperSearchClient(RecordingPost({}), "k", num=0)


def test_build_serper_search_reads_env_only_inside_factory(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(AuthenticationFailure):
        build_serper_search()
    monkeypatch.setenv("SERPER_API_KEY", "abc")
    post = RecordingPost({"news": []})
    client = build_serper_search(mode="news", tbs="qdr:y", http_post=post)
    client.search(SearchRequest("q"))
    assert post.calls[0][2]["X-API-KEY"] == "abc"


def test_observation_excerpt_is_capped():
    excerpt = observation_excerpt({"title": "t", "snippet": "x" * 5000})
    assert len(excerpt) <= 2000
