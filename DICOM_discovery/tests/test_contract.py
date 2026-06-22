"""Phase 0 — the versioned, machine-readable verdict contract.

These tests pin the output *contract* that turns the package from "something that
emits HTML" into a tool whose verdict is a reusable, schema-validated artifact.
Like the rest of the suite, they build the payload from the real synthetic cohort
fixtures (no hand-mocked DataFrames).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import __version__  # noqa: E402
from DICOM_discovery.rt_integrity import build_rt_rollup  # noqa: E402


def _rollup(cohort):
    _df, idx, _truth = cohort
    return build_rt_rollup(idx.table)


class TestVerdictPayload:
    def test_payload_validates_against_schema(self, cohort):
        from DICOM_discovery.contract import build_verdict_payload, validate_payload

        payload = build_verdict_payload(_rollup(cohort))
        # Must not raise: the payload is its own schema's instance.
        validate_payload(payload)

    def test_payload_carries_schema_and_tool_version(self, cohort):
        from DICOM_discovery.contract import SCHEMA_VERSION, build_verdict_payload

        payload = build_verdict_payload(_rollup(cohort))
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["tool_version"] == __version__

    def test_one_patient_entry_per_rollup_row_with_verdict_and_reason(self, cohort):
        from DICOM_discovery.contract import build_verdict_payload

        rollup = _rollup(cohort)
        payload = build_verdict_payload(rollup)
        assert len(payload["patients"]) == len(rollup)
        ids = {p["patient_id"] for p in payload["patients"]}
        assert ids == set(rollup["patient_id"])
        for p in payload["patients"]:
            assert p["verdict"] in {"OK", "WARN", "INCOMPLETE", "NO_RT"}
            assert "reason" in p

    def test_each_patient_carries_a_recommended_action(self, cohort):
        from DICOM_discovery.contract import build_verdict_payload

        payload = build_verdict_payload(_rollup(cohort))
        for p in payload["patients"]:
            assert isinstance(p["action"], str)
            # An actionable verdict must name a next step; settled ones need none.
            if p["verdict"] in {"WARN", "INCOMPLETE"}:
                assert p["action"] != ""


class TestValidation:
    def test_rejects_payload_missing_schema_version(self, cohort):
        import jsonschema

        from DICOM_discovery.contract import build_verdict_payload, validate_payload

        payload = build_verdict_payload(_rollup(cohort))
        del payload["schema_version"]
        with pytest.raises(jsonschema.ValidationError):
            validate_payload(payload)

    def test_rejects_unknown_verdict_value(self, cohort):
        import jsonschema

        from DICOM_discovery.contract import build_verdict_payload, validate_payload

        payload = build_verdict_payload(_rollup(cohort))
        payload["patients"][0]["verdict"] = "MAYBE"
        with pytest.raises(jsonschema.ValidationError):
            validate_payload(payload)


class TestProvenance:
    def test_run_embeds_manifest_and_content_hash(self, cohort):
        from DICOM_discovery.contract import build_verdict_payload

        _df, idx, _truth = cohort
        payload = build_verdict_payload(_rollup(cohort), manifest=idx.manifest,
                                        protocol_name="brain_rt_followup")
        assert payload["protocol"] == "brain_rt_followup"
        assert payload["run"]["n_patients"] == idx.manifest["n_patients"]
        assert len(payload["run"]["content_hash"]) == 64  # sha256 hex digest

    def test_content_hash_is_deterministic_and_verdict_sensitive(self, cohort):
        from DICOM_discovery.contract import build_verdict_payload

        rollup = _rollup(cohort)
        h1 = build_verdict_payload(rollup)["run"]["content_hash"]
        h2 = build_verdict_payload(rollup)["run"]["content_hash"]
        assert h1 == h2  # same input → same fingerprint

        flipped = rollup.copy()
        i0 = flipped.index[0]
        current = flipped.loc[i0, "rt_status"]
        flipped.loc[i0, "rt_status"] = "WARN" if current != "WARN" else "OK"
        h3 = build_verdict_payload(flipped)["run"]["content_hash"]
        assert h3 != h1  # a changed verdict changes the provenance hash
