"""The versioned, machine-readable verdict contract.

The HTML report is *one* projection of this payload. Emitting the verdicts as a
schema-validated JSON document — with a run manifest for provenance — is what makes
the tool's output reusable and reproducible by a third party: the verdict can be
re-run, diffed and audited without re-parsing HTML.

``schema_version`` is bumped only on a breaking change to the payload shape; the
package version (``tool_version``) moves independently.
"""
from __future__ import annotations

import datetime
import hashlib
from typing import Dict, List, Optional

import pandas as pd

from . import __version__

SCHEMA_VERSION = "1.0"

#: Verdict scale, kept in lock-step with ``rt_integrity.Status``.
_VERDICTS = ["OK", "WARN", "INCOMPLETE", "NO_RT"]

VERDICTS_SCHEMA: Dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "DICOM_discovery cohort verdicts",
    "type": "object",
    "required": ["schema_version", "tool", "tool_version", "generated_utc", "patients"],
    "properties": {
        "schema_version": {"type": "string"},
        "tool": {"type": "string"},
        "tool_version": {"type": "string"},
        "generated_utc": {"type": "string"},
        "protocol": {"type": ["string", "null"]},
        "run": {"type": "object"},
        "patients": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["patient_id", "verdict", "reason"],
                "properties": {
                    "patient_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": _VERDICTS},
                    "reason": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

#: rollup columns surfaced verbatim into each patient entry (besides verdict/reason).
_PASSTHROUGH = [
    "action",
    "n_studies", "n_rt_studies", "fragmented",
    "has_CT", "has_RTSTRUCT", "has_RTDOSE", "has_RTPLAN",
    "roi_GTV", "roi_CTV", "roi_PTV",
    "n_roi_nonstandard", "roi_nonstandard",
]


def _content_hash(rollup_df: pd.DataFrame) -> str:
    """Stable digest of the verdict table — provenance fingerprint of the run."""
    if rollup_df is None or rollup_df.empty:
        return hashlib.sha256(b"").hexdigest()
    payload = rollup_df.sort_values("patient_id").to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _patient_entry(row: dict) -> dict:
    entry = {
        "patient_id": str(row["patient_id"]),
        "verdict": str(row["rt_status"]),
        "reason": str(row.get("reason", "") or ""),
    }
    for col in _PASSTHROUGH:
        if col in row:
            val = row[col]
            entry[col] = bool(val) if isinstance(val, bool) else val
    return entry


def build_verdict_payload(
    rollup_df: pd.DataFrame,
    *,
    manifest: Optional[Dict] = None,
    protocol_name: Optional[str] = None,
) -> Dict:
    """Assemble the verdict payload from the per-patient rollup table.

    ``manifest`` is the indexer run manifest (provenance); it is embedded under
    ``run`` together with a content hash so the document is self-describing.
    """
    rows: List[dict] = [] if rollup_df is None or rollup_df.empty else rollup_df.to_dict("records")
    run = dict(manifest or {})
    run["content_hash"] = _content_hash(rollup_df)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "DICOM_discovery",
        "tool_version": __version__,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "protocol": protocol_name,
        "run": run,
        "patients": [_patient_entry(r) for r in rows],
    }


def validate_payload(payload: Dict) -> None:
    """Validate ``payload`` against :data:`VERDICTS_SCHEMA`. Raises on violation."""
    import jsonschema

    jsonschema.validate(instance=payload, schema=VERDICTS_SCHEMA)
