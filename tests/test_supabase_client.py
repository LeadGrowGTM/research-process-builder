"""
RED test for Issue #12: pipeline_base.py ~200 lines Supabase CRUD.

Problem: Storage implementation mixed into pipeline orchestration.
ResearchPipeline has 7 Supabase methods totalling ~180 lines — all HTTP
plumbing that has nothing to do with pipeline orchestration.

Solution: Extract SupabaseClient class. Pipeline delegates.

SupabaseClient must:
  - Hold url + key config (injectable for testing)
  - Expose: headers(), table_exists(table), push_rows(table, rows, date_str),
    fetch_recent(table, days), create_table(table, schema_sql)
  - Be importable from pipeline_base

ResearchPipeline must:
  - Delegate Supabase calls to an injected/default SupabaseClient
  - Retain the same external behaviour (no API surface changes)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pipeline_base import SupabaseClient


class TestSupabaseClientInterface:
    """SupabaseClient must be a standalone class importable from pipeline_base."""

    def setup_method(self):
        self.client = SupabaseClient(url="https://fake.supabase.co", key="fakekey")

    def test_supabase_client_is_importable(self):
        """SupabaseClient must be a class, not a function."""
        assert isinstance(self.client, SupabaseClient)

    def test_headers_returns_dict_with_required_keys(self):
        h = self.client.headers()
        assert "apikey" in h
        assert "Authorization" in h
        assert "Content-Type" in h

    def test_headers_prefer_adds_prefer_key(self):
        h = self.client.headers(prefer="return=minimal")
        assert h["Prefer"] == "return=minimal"

    def test_headers_no_prefer_omits_prefer_key(self):
        h = self.client.headers()
        assert "Prefer" not in h

    def test_table_exists_returns_bool(self):
        """table_exists() must return bool. With fake URL it should return False gracefully."""
        result = self.client.table_exists("some_table")
        assert isinstance(result, bool)

    def test_fetch_recent_returns_list_without_credentials(self):
        """fetch_recent() with no real credentials must return empty list (not raise)."""
        empty_client = SupabaseClient(url=None, key=None)
        result = empty_client.fetch_recent("some_table", days=30)
        assert isinstance(result, list)
        assert result == []

    def test_push_rows_returns_int_without_credentials(self):
        """push_rows() with no real credentials must return 0 (not raise)."""
        empty_client = SupabaseClient(url=None, key=None)
        rows = [{"company_name": "Test Co", "source_url": "https://example.com"}]
        result = empty_client.push_rows("test_table", rows, "2026-06-01", recent=[])
        assert isinstance(result, int)
        assert result == 0


class TestPipelineDelegatesToSupabaseClient:
    """ResearchPipeline must delegate Supabase I/O to SupabaseClient."""

    def test_pipeline_has_no_supabase_headers_method(self):
        """
        After extraction, ResearchPipeline.supabase_headers() should no longer
        exist as a method — its logic now lives in SupabaseClient.headers().
        """
        from pipeline_base import ResearchPipeline
        pipeline = ResearchPipeline()
        assert not hasattr(pipeline, "supabase_headers"), (
            "supabase_headers() should be removed from ResearchPipeline — "
            "it belongs on SupabaseClient.headers()"
        )

    def test_pipeline_has_no_check_supabase_table_method(self):
        """check_supabase_table() should be removed from ResearchPipeline."""
        from pipeline_base import ResearchPipeline
        pipeline = ResearchPipeline()
        assert not hasattr(pipeline, "check_supabase_table"), (
            "check_supabase_table() should be removed from ResearchPipeline — "
            "it belongs on SupabaseClient.table_exists()"
        )

    def test_pipeline_has_no_fetch_recent_companies_method(self):
        """fetch_recent_companies() should be removed from ResearchPipeline."""
        from pipeline_base import ResearchPipeline
        pipeline = ResearchPipeline()
        assert not hasattr(pipeline, "fetch_recent_companies"), (
            "fetch_recent_companies() should be removed from ResearchPipeline — "
            "it belongs on SupabaseClient.fetch_recent()"
        )

    def test_pipeline_has_no_push_to_supabase_method(self):
        """push_to_supabase() should be removed from ResearchPipeline."""
        from pipeline_base import ResearchPipeline
        pipeline = ResearchPipeline()
        assert not hasattr(pipeline, "push_to_supabase"), (
            "push_to_supabase() should be removed from ResearchPipeline — "
            "it belongs on SupabaseClient.push_rows()"
        )

    def test_pipeline_write_output_calls_supabase_client(self):
        """
        write_output() must use the SupabaseClient for storage, not inline HTTP.
        We inject a mock client and verify it receives the push call.
        """
        from pipeline_base import ResearchPipeline
        import tempfile, os

        pipeline = ResearchPipeline()
        pipeline.OUTPUT_PREFIX = "test"
        pipeline.OUTPUT_FIELDNAMES = ["date", "company_name", "company_domain", "score"]
        pipeline.SUPABASE_TABLE = "test_table"

        # Patch the output directory to a temp dir
        import pipeline_base as pb
        orig_output_dir = pb.OUTPUT_DIR

        with tempfile.TemporaryDirectory() as tmpdir:
            pb.OUTPUT_DIR = Path(tmpdir)
            pb.STAGE_DIR = Path(tmpdir) / "stages"
            pb.STAGE_DIR.mkdir()

            mock_client = MagicMock()
            mock_client.table_exists.return_value = True
            mock_client.push_rows.return_value = 1
            pipeline._supabase = mock_client

            enriched = [
                {"company_name": "Acme", "company_domain": "acme.com",
                 "score": 5, "confidence": "high"}
            ]
            pipeline.write_output(enriched, "2026-06-01")

            mock_client.push_rows.assert_called_once()

        pb.OUTPUT_DIR = orig_output_dir
