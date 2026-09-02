"""CLI for the ``news-product-launches`` enrichment package.

Every package ships one of these so a consumer can work the enrichment without
importing this repository's internals:

    py enrichments/news-product-launches/run.py describe
    py enrichments/news-product-launches/run.py emit
    py enrichments/news-product-launches/run.py body [--variant X]
    py enrichments/news-product-launches/run.py render --company-name X --domain x.com
    py enrichments/news-product-launches/run.py execute --lineage <name> --allow-paid

``describe`` is the card a registry indexes; ``emit`` renders the
gtm_orchestrator CATALOG entry this package installs as.

``body`` and ``render`` are the two prompt views and they are not
interchangeable. ``body`` writes the prompt body alone, after any ``--variant``
overlay - that is the file a revalidation run wants, so
``run.py body --variant X > output/X.md`` then ``--prompt output/X.md`` on the
loop is how a variant is materialised and scored. ``render`` is for reading: it
composes the body the way the live client does, with the ``Company ID``,
``Subject company``, ``Enrichment``, ``Requested fields``, and ``Evidence``
sections the run appends, so a prompt edit can be reviewed before it costs
anything. Feeding a ``render`` to ``--prompt`` would send those placeholder
sections to the model alongside the real ones.

``execute`` revalidates the package against its sealed benchmark corpus by
delegating to the one vetted loop entry point rather than keeping a second copy
of the collect-and-call code. That loop scores the whole corpus under a lineage
name; it is not a per-company lookup, so ``execute`` refuses ``--company-name``,
``--domain``, ``--as-of``, and ``--variant`` instead of accepting them and
silently ignoring them. Per-company enrichment runs through the GTM orchestrator
once the package is installed there. ``execute`` also refuses to spend without
``--allow-paid``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
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

REVALIDATE_ENTRYPOINT = "scripts/company_enrichment_news_loop.py"
SUBJECT_FLAGS = ("--company-name", "--domain", "--as-of", "--variant")


def live_command(lineage: str) -> tuple[str, ...]:
    """The vetted corpus revalidation run for this package under ``lineage``."""
    return (
        sys.executable,
        REVALIDATE_ENTRYPOINT,
        "--evaluate",
        "--lineage",
        lineage,
        "--allow-paid",
    )


def quote_command(command: tuple[str, ...]) -> str:
    """``command`` as one line an operator can paste back into this shell.

    ``sys.executable`` is routinely a spaced path on Windows, so a bare space
    join hands back a line that does not run.
    """
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def _inputs(args: argparse.Namespace) -> dict[str, str]:
    values = {"company_name": args.company_name or "", "domain": args.domain or ""}
    if args.as_of:
        values["as_of"] = args.as_of
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("describe", "emit", "body", "render", "execute")
    )
    parser.add_argument("--company-name")
    parser.add_argument("--domain")
    parser.add_argument("--as-of")
    parser.add_argument("--variant", help="name of a file in variants/")
    parser.add_argument(
        "--lineage", help="artifact lineage name that labels an execute run"
    )
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

    if args.mode == "body":
        print(package.body.strip())
        return 0

    if args.mode == "render":
        try:
            print(render(package, _inputs(args)))
        except PackageError as error:
            print(f"input error: {error}", file=sys.stderr)
            return 2
        return 0

    supplied = [
        flag
        for flag, value in zip(
            SUBJECT_FLAGS, (args.company_name, args.domain, args.as_of, args.variant)
        )
        if value
    ]
    if supplied:
        print(
            f"execute revalidates {package.id} against its sealed benchmark corpus "
            f"under a lineage name; it cannot honour {', '.join(supplied)}. "
            "Render the prompt for one company with 'render --company-name X "
            "--domain x.com', and run per-company enrichment through the GTM "
            "orchestrator once this package is installed there.",
            file=sys.stderr,
        )
        return 2
    if not args.lineage:
        print("execute needs --lineage <name> to label its run artifacts", file=sys.stderr)
        return 2

    command = live_command(args.lineage)
    if not args.allow_paid:
        print(quote_command(command))
        print("refusing to spend without --allow-paid", file=sys.stderr)
        return 3
    return subprocess.call(command, cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
