"""Phase 3 — light AAPM TG-263 ROI-nomenclature conformance.

TG-263 (Mayo et al., 2018) standardises target/OAR structure names. This is a
*bounded* check focused on brain-RT structures: it flags non-standard ROI names
as an additive signal and never changes the OK/WARN/INCOMPLETE verdict scale.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery.rt_integrity import build_rt_rollup  # noqa: E402
from DICOM_discovery.tg263 import is_tg263_conformant, nonconformant_rois  # noqa: E402


class TestConformance:
    @pytest.mark.parametrize("name", ["GTV", "CTV", "PTV", "GTV_boost", "Brainstem",
                                      "SpinalCord", "OpticChiasm", "Cochlea_L", "OpticNerve_R",
                                      "Parotid_L", "Hippocampus_R"])
    def test_standard_names_are_conformant(self, name):
        assert is_tg263_conformant(name) is True

    @pytest.mark.parametrize("name", ["ptv", "Brain Stem", "Parotid LT", "Cochlea_left",
                                      "gtv1", "OpticNerveLeft", "RightParotid"])
    def test_off_nomenclature_names_are_flagged(self, name):
        assert is_tg263_conformant(name) is False

    def test_nonconformant_rois_returns_only_the_bad_ones(self):
        names = ["GTV", "CTV", "Brain Stem", "ptv", "Brainstem"]
        bad = nonconformant_rois(names)
        assert set(bad) == {"Brain Stem", "ptv"}


class TestRollupIntegration:
    def test_clean_cohort_has_a_nonstandard_count_column_all_zero(self, cohort):
        _df, idx, _truth = cohort
        rollup = build_rt_rollup(idx.table)
        assert "n_roi_nonstandard" in rollup.columns
        # The synthetic cohort uses TG-263-conformant names throughout.
        assert (rollup["n_roi_nonstandard"] == 0).all()

    def test_verdict_scale_is_unchanged_by_the_check(self, cohort):
        # TG-263 is additive: the known ground-truth verdicts must be untouched.
        _df, idx, truth = cohort
        rollup = build_rt_rollup(idx.table).set_index("patient_id")
        for pid, gt in truth.items():
            assert rollup.loc[pid, "rt_status"] == gt.expected_status
