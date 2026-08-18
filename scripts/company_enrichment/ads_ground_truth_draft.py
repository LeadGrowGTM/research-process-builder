"""Pre-fill running-ads ground truth from a collected signal dossier.

The deterministic ``ads`` assertion already knows each channel's status,
landing page, call to action, and Evidence IDs. The offer fields need human
reading of the Meta ad copy, so they are left as ``TODO_HUMAN`` (which the
sealed-dataset validator rejects). Drafts always land in
``ground-truth-drafts/``, never in the sealed ``ground-truth/`` directory.
"""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path
from typing import Any

import yaml

from .ads_contracts import AD_CHANNELS, ADS_FIELD
from .contracts import CompanyDossier


ENRICHMENT_ID = "running-ads-offer-intelligence"
TODO_HUMAN = "TODO_HUMAN"


def drafts_dir(repo_root: Path) -> Path:
    return Path(repo_root) / "benchmarks" / "signals" / ENRICHMENT_ID / "ground-truth-drafts"


def _as_of(dossier: CompanyDossier, cited: set[str], today: date | None) -> str:
    dates = [item.retrieved_at.date() for item in dossier.evidence if item.evidence_id in cited]
    return (max(dates) if dates else today or date.today()).isoformat()


def draft_ads_ground_truth(
    signal_dossier: CompanyDossier, *, today: date | None = None,
) -> dict[str, Any]:
    """Return the ground-truth YAML mapping for one company, ready for human review."""
    assertion = next(
        (item for item in signal_dossier.assertions if item.field == ADS_FIELD), None,
    )
    channels: dict[str, Any] = {}
    cited: set[str] = set()
    if assertion is None:
        channels = {name: {"status": "unknown"} for name in AD_CHANNELS}
    else:
        for item in assertion.value["channels"]:
            entry: dict[str, Any] = {"status": item["status"]}
            if item["status"] != "unknown":
                entry["evidence_ids"] = list(item["evidence_ids"])
                cited.update(item["evidence_ids"])
            if item["channel"] == "meta" and item["status"] != "unknown":
                entry.update({
                    "landing_page": item.get("landing_page"),
                    "call_to_action": item.get("call_to_action"),
                    "observed_offer": TODO_HUMAN,
                    "offer_aliases": [TODO_HUMAN],
                })
            channels[item["channel"]] = entry
    return {
        "company_id": signal_dossier.company_id,
        "as_of": _as_of(signal_dossier, cited, today),
        "channels": channels,
    }


def write_ads_ground_truth_draft(
    repo_root: Path, signal_dossier: CompanyDossier, *, today: date | None = None,
) -> Path:
    target = drafts_dir(repo_root) / f"{signal_dossier.company_id}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        draft_ads_ground_truth(signal_dossier, today=today),
        allow_unicode=True, sort_keys=True, default_flow_style=False,
    )
    temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, target)
    return target
