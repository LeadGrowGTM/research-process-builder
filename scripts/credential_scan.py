"""Scan tracked text for literal credential assignments without exposing values."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys

_GIT_ROUTING_VARIABLES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
_ALLOWED_PLACEHOLDERS = frozenset({"user-approval-token", "YOUR_PRIVATE_API_KEY"})
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*[\"']([^\"'${][^\"']*)[\"']"
)


@dataclass(frozen=True, slots=True)
class Finding:
    source: str
    line_number: int
    field: str

    @property
    def safe_message(self) -> str:
        return f"{self.source}:{self.line_number}: literal value assigned to {self.field}"


def find_literal_credentials(text: str, source: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in _ASSIGNMENT.finditer(line):
            if match.group(2) in _ALLOWED_PLACEHOLDERS:
                continue
            findings.append(Finding(source, line_number, match.group(1)))
    return findings


def _clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in _GIT_ROUTING_VARIABLES:
        environment.pop(name, None)
    return environment


def scan_repository(root: Path) -> list[Finding]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, env=_clean_git_environment(),
        check=True, capture_output=True,
    )
    findings: list[Finding] = []
    for raw_name in result.stdout.split(b"\0"):
        if not raw_name:
            continue
        relative = raw_name.decode("utf-8", "surrogateescape")
        path = root / relative
        if path.is_symlink() or not path.is_file():
            continue
        try:
            content = path.read_bytes()
            if b"\0" in content:
                continue
            text = content.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(find_literal_credentials(text, relative))
    return findings


def main() -> int:
    root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], env=_clean_git_environment(),
        check=True, text=True, capture_output=True,
    )
    findings = scan_repository(Path(root_result.stdout.strip()))
    for finding in findings:
        print(finding.safe_message)
    if findings:
        print(f"CREDENTIAL_SCAN=FAIL violations={len(findings)}", file=sys.stderr)
        return 1
    print("CREDENTIAL_SCAN=PASS violations=0 allowed_placeholders=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())