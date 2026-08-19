from __future__ import annotations

from datetime import date

import pytest

from scripts.company_enrichment.adapters.parallel import (
    PARALLEL_SEARCH_COST_USD, ParallelSearchAdapter, ParallelSearchClient,
    build_parallel_search, parallel_observation_excerpt,
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


def _client(response, **kwargs):
    return ParallelSearchClient(
        RecordingPost(response), "secret-key", today=lambda: date(2026, 8, 19), **kwargs,
    )


def test_search_posts_to_v1_search_and_folds_labelled_excerpt():
    post = RecordingPost({"results": [
        {"url": "https://www.prnewswire.com/a", "title": "AgencyAnalytics Launches X",
         "publish_date": "2024-03-05", "excerpts": ["Today announced", "a new feature"]},
        {"title": "no url", "excerpts": ["dropped"]},
        {"url": "ftp://example.com/x", "excerpts": ["dropped"]},
    ]})
    client = ParallelSearchClient(post, "secret-key")

    result = client.search(SearchRequest("agencyanalytics.com news"))

    url, payload, headers = post.calls[0]
    assert url == "https://api.parallel.ai/v1/search"
    assert payload["search_queries"] == ["agencyanalytics.com news"]
    assert payload["mode"] == "advanced"
    assert payload["advanced_settings"]["max_results"] == 10
    assert "objective" not in payload
    assert headers["x-api-key"] == "secret-key"
    assert headers["Content-Type"] == "application/json"
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.url == "https://www.prnewswire.com/a"
    assert observation.excerpt == (
        "Title: AgencyAnalytics Launches X\nDate: 2024-03-05\nSource: prnewswire.com\n"
        "Snippet: Today announced a new feature"
    )
    assert client.provider == "parallel"
    assert client.cost_per_query_usd == PARALLEL_SEARCH_COST_USD["advanced"] == "0.005"


def test_news_serp_mode_sets_objective_and_tbs_maps_to_after_date():
    post = RecordingPost({"results": []})
    client = ParallelSearchClient(
        post, "k", serp_mode="news", tbs="qdr:y", today=lambda: date(2026, 8, 19),
    )

    client.search(SearchRequest("acme news"))

    _, payload, _ = post.calls[0]
    assert "acme news" in payload["objective"]
    assert payload["advanced_settings"]["source_policy"] == {"after_date": "2025-08-19"}
    assert payload["advanced_settings"]["excerpt_settings"] == {"max_chars_per_result": 1200}


def test_request_objective_overrides_the_mode_default():
    post = RecordingPost({"results": []})
    client = ParallelSearchClient(post, "k", serp_mode="news")

    client.search(SearchRequest("acme news", objective="Find dated acme launches."))

    assert post.calls[0][1]["objective"] == "Find dated acme launches."


def test_unknown_tbs_window_is_ignored():
    post = RecordingPost({"results": []})
    _client(post.response, serp_mode="web", tbs="cdr:custom")
    client = ParallelSearchClient(post, "k", tbs="cdr:custom")
    client.search(SearchRequest("q"))
    assert "source_policy" not in post.calls[0][1]["advanced_settings"]


def test_for_query_derives_sibling_client_sharing_transport():
    post = RecordingPost({"results": []})
    client = ParallelSearchClient(post, "k", search_mode="fast")

    sibling = client.for_query(mode="news", tbs="qdr:y")
    assert sibling is not client and sibling.mode == "news" and sibling.tbs == "qdr:y"
    assert sibling.search_mode == "fast"
    assert client.for_query(mode="web", tbs=None) is client
    assert sibling.search(SearchRequest("q")).observations == ()
    assert post.calls[0][1]["mode"] == "fast"


def test_missing_results_key_is_zero_observations_not_failure():
    assert _client({"search_id": "s"}).search(SearchRequest("q")).observations == ()


@pytest.mark.parametrize("response, error", [
    (AuthenticationFailure("rejected"), AuthenticationFailure),
    (RuntimeError("socket"), RetryableFailure),
    ("not an object", ContractFailure),
    ({"results": "nope"}, ContractFailure),
])
def test_failures_are_typed(response, error):
    with pytest.raises(error):
        _client(response).search(SearchRequest("q"))


def test_transport_failure_message_never_carries_key():
    client = ParallelSearchClient(RecordingPost(RuntimeError("leak secret-key")), "secret-key")
    with pytest.raises(RetryableFailure) as info:
        client.search(SearchRequest("q"))
    assert "secret-key" not in str(info.value)


def test_client_rejects_bad_configuration():
    with pytest.raises(AuthenticationFailure):
        ParallelSearchClient(RecordingPost({}), "")
    with pytest.raises(ValueError):
        ParallelSearchClient(RecordingPost({}), "k", search_mode="mega")
    with pytest.raises(ValueError):
        ParallelSearchClient(RecordingPost({}), "k", serp_mode="images")
    with pytest.raises(ValueError):
        ParallelSearchClient(RecordingPost({}), "k", num=0)


def test_build_parallel_search_reads_env_only_inside_factory(monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    with pytest.raises(AuthenticationFailure):
        build_parallel_search()
    monkeypatch.setenv("PARALLEL_API_KEY", "abc")
    post = RecordingPost({"results": []})
    client = build_parallel_search(http_post=post)
    assert isinstance(client, ParallelSearchClient)
    client.search(SearchRequest("q"))
    assert post.calls[0][2]["x-api-key"] == "abc"


def test_build_parallel_search_keeps_legacy_injected_bridge(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "abc")
    adapter = build_parallel_search(search=lambda query: [])
    assert isinstance(adapter, ParallelSearchAdapter)
    assert adapter.search(SearchRequest("q")).observations == ()


def test_observation_excerpt_is_capped():
    excerpt = parallel_observation_excerpt(
        {"url": "https://a.example/x", "title": "t", "excerpts": ["x" * 5000]}
    )
    assert len(excerpt) <= 2000
