"""News and product launches signal loop (collect, draft ground truth, evaluate).

    py scripts/company_enrichment_news_loop.py --collect --dry-run
    lg run -- py scripts/company_enrichment_news_loop.py --collect [--company saas-01] [--overwrite]
    py scripts/company_enrichment_news_loop.py --draft-ground-truth [--company saas-01] [--overwrite]
    py scripts/company_enrichment_news_loop.py --evaluate --lineage <name> --dry-run
    lg run -- py scripts/company_enrichment_news_loop.py --evaluate --lineage <name> --allow-paid
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.company_enrichment.signal_entrypoints import (  # noqa: E402
    build_news_spec, draft_news_ground_truth, run_entrypoint,
)


def main(argv=None, **kwargs) -> int:
    return run_entrypoint(build_news_spec(), draft_news_ground_truth, argv, **kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
