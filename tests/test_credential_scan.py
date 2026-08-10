"""Fail-closed repository credential scan contracts."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from credential_scan import find_literal_credentials


def test_known_documentation_placeholders_are_not_credentials():
    text = '''token: "user-approval-token"\napi_key: 'YOUR_PRIVATE_API_KEY'\n'''
    assert find_literal_credentials(text, "docs/example.md") == []


def test_literal_credential_values_are_reported_with_source_and_line():
    findings = find_literal_credentials(
        '''password = "correct-horse-battery"\nAPI_KEY: 'sk-live-real-looking-value'\n''',
        "config/example.toml",
    )
    assert [(item.source, item.line_number, item.field) for item in findings] == [
        ("config/example.toml", 1, "password"),
        ("config/example.toml", 2, "API_KEY"),
    ]
    assert all("correct-horse" not in item.safe_message and "sk-live" not in item.safe_message for item in findings)