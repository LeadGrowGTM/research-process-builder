"""Compatibility CLI delegating to the canonical orchestration module."""

from research_orchestration.cli import main


if __name__ == "__main__":
    raise SystemExit(main())