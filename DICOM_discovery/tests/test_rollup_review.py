"""Phase 1 — verdict model: each patient carries a recommended *action*, and the
rollup can be ordered for review so the actionable cases (WARN/INCOMPLETE) float
to the top. Built from the real synthetic cohort (ground-truth P001..P007).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery.rt_integrity import build_rt_rollup, order_for_review  # noqa: E402


def _rollup(cohort):
    _df, idx, _truth = cohort
    return build_rt_rollup(idx.table)


def _action_for(rollup, pid):
    return rollup.set_index("patient_id").loc[pid, "action"]


class TestAction:
    def test_every_row_has_a_string_action(self, cohort):
        rollup = _rollup(cohort)
        assert "action" in rollup.columns
        assert rollup["action"].map(lambda a: isinstance(a, str)).all()

    def test_ok_patients_have_no_action_actionable_ones_do(self, cohort):
        rollup = _rollup(cohort)
        assert _action_for(rollup, "P001") == ""  # OK → nothing to do
        assert _action_for(rollup, "P002") == ""
        for pid in ("P003", "P004", "P005", "P006", "P007"):
            assert _action_for(rollup, pid) != ""

    def test_incomplete_recommends_pacs_retrieval(self, cohort):
        # P006 is missing RTDOSE → go fetch it.
        assert "PACS" in _action_for(_rollup(cohort), "P006")

    def test_missing_target_recommends_contouring(self, cohort):
        # P007 is missing the PTV target → contour it.
        assert "contour" in _action_for(_rollup(cohort), "P007").lower()

    def test_broken_reference_recommends_checking_the_link(self, cohort):
        # P003/P004 have an unresolved reference link → verify the link.
        assert "lien" in _action_for(_rollup(cohort), "P003").lower()


class TestOrderForReview:
    def test_actionable_cases_float_to_the_top(self, cohort):
        ordered = order_for_review(_rollup(cohort))
        verdicts = list(ordered["rt_status"])
        actionable = {"WARN", "INCOMPLETE"}
        # No settled verdict (OK/NO_RT) appears before any actionable one.
        first_settled = next((i for i, v in enumerate(verdicts) if v not in actionable), len(verdicts))
        assert all(v in actionable for v in verdicts[:first_settled])
        assert verdicts[0] in actionable

    def test_preserves_every_patient(self, cohort):
        rollup = _rollup(cohort)
        ordered = order_for_review(rollup)
        assert set(ordered["patient_id"]) == set(rollup["patient_id"])
        assert len(ordered) == len(rollup)
