"""CLI for the ``news-product-launches`` enrichment package.

Every package ships one of these so a consumer can work the enrichment without
importing this repository's internals:

    py enrichments/news-product-launches/run.py describe
    py enrichments/news-product-launches/run.py emit
    py enrichments/news-product-launches/run.py render --company-name X --domain x.com
    py enrichments/news-product-launches/run.py execute --company-name X --domain x.com --allow-paid

``describe`` is the card a registry indexes; ``emit`` renders the
gtm_orchestrator CATALOG entry this package installs as. ``render`` is the exact prompt text
that would be sent, after any ``--variant`` overlay, so a prompt edit can be
reviewed before it costs anything. ``execute`` refuses to spend money without
``--allow-paid``; the live path stays the one vetted loop entry point rather
than a second copy of the collect-and-call code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.company_enrichment.packages import (  # noqa: E402
    PackageError,
    emit_registry_entry,
    load_package,
    render,
)

LIVE_COMMAND = (
    sys.executable,
    "-m",
    "scripts.company_enrichment.signal_loop",
    "--enrichment",
    "news-product-launches",
)


def _inputs(args: argparse.Namespace) -> dict[str, str]:
    values = {"company_name": args.company_name or "", "domain": args.domain or ""}
    if args.as_of:
        values["as_of"] = args.as_of
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("describe", "emit", "render", "execute"))
    parser.add_argument("--company-name")
    parser.add_argument("--domain")
    parser.add_argument("--as-of")
    parser.add_argument("--variant", help="name of a file in variants/")
    parser.add_argument(
        "--allow-paid",
        action="store_true",
        help="required for execute; without it the command is printed, not run",
    )
    args = parser.parse_args(argv)

    try:
        package = load_package(PACKAGE_ROOT, variant=args.variant)
    except PackageError as error:
        print(f"package error: {error}", file=sys.stderr)
        return 2

    if args.mode == "describe":
        print(json.dumps(package.card(), indent=2, sort_keys=True))
        return 0

    if args.mode == "emit":
        try:
            print(emit_registry_entry(package))
        except PackageError as error:
            print(f"emit error: {error}", file=sys.stderr)
            return 2
        return 0

    try:
        prompt = render(package, _inputs(args))
    except PackageError as error:
        print(f"input error: {error}", file=sys.stderr)
        return 2

    if args.mode == "render":
        print(prompt)
        return 0

    if package.revalidation == "required":
        print(
            f"variant {package.variant!r} changes what was scored; "
            f"rerun {package.adaptation.get('revalidate_with', 'the loop')} before a live run",
            file=sys.stderr,
        )
        return 3
    if not args.allow_paid:
        print(" ".join(LIVE_COMMAND))
        print("refusing to spend without --allow-paid", file=sys.stderr)
        return 3
    return subprocess.call(LIVE_COMMAND, cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
