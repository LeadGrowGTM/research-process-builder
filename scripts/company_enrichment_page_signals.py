"""CLI for deterministic careers/blog page-presence signals.

Free HTTP checks only - no model, no paid provider. Reads company domains
from the benchmark corpus (or explicit --domain flags) and journals one
JSON record per company under runs/company-enrichment/page-signals/.

Usage (from the repository root):
    py scripts/company_enrichment_page_signals.py --lineage page-signals-v1
    py scripts/company_enrichment_page_signals.py --domain example.com
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.company_enrichment.corpus import Corpus
from scripts.company_enrichment.page_signals import run_corpus


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check careers and blog page presence on company websites "
            "through a deterministic path waterfall."
        ),
    )
    parser.add_argument(
        "--companies", type=Path, default=Path("benchmarks/companies.yaml"),
        help="Corpus YAML supplying company ids and domains.",
    )
    parser.add_argument(
        "--domain", action="append", dest="domains",
        help="Check this domain instead of the corpus (repeatable).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Check only the first N corpus companies.",
    )
    parser.add_argument(
        "--lineage", default="page-signals-adhoc",
        help="Run name; artifacts land under "
             "runs/company-enrichment/page-signals/<lineage>/.",
    )
    args = parser.parse_args(argv)
    if args.domains:
        companies = [(item, item) for item in args.domains]
    else:
        corpus = Corpus.load(args.companies)
        companies = [(item.id, item.domain) for item in corpus.fixtures]
        if args.limit is not None:
            companies = companies[: args.limit]
    output_dir = Path("runs/company-enrichment/page-signals") / args.lineage
    summary = run_corpus(companies, output_dir=output_dir)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
