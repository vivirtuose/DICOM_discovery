"""Light AAPM TG-263 ROI-nomenclature conformance.

TG-263 (Mayo et al., *Practical Radiation Oncology*, 2018) defines a standard
nomenclature for target and organ-at-risk structures: CamelCase base names, no
spaces, laterality as an ``_L`` / ``_R`` suffix, and uppercase target prefixes
(GTV/CTV/PTV …) optionally followed by a custom ``_suffix``.

This is a deliberately *bounded* check — a curated brain-RT subset, not the full
TG-263 table — used as an additive QC signal. It never decides the
OK/WARN/INCOMPLETE verdict; it only flags names that fall outside the convention.
"""
from __future__ import annotations

import re
from typing import Iterable, List

#: Uppercase target-volume prefixes, optionally with a custom ``_suffix``.
_TARGET_RE = re.compile(r"^(GTV|CTV|PTV|ITV|IGTV)(_[A-Za-z0-9]+)?$")

#: Curated TG-263 base names (laterality stripped) relevant to brain radiotherapy.
_TG263_OAR_BASES = frozenset({
    "Brain", "Brainstem", "SpinalCord", "OpticChiasm", "OpticNerve", "Eye",
    "Globe", "Lens", "Retina", "Cornea", "LacrimalGland", "Cochlea",
    "Hippocampus", "Pituitary", "Hypothalamus", "TemporalLobe", "Parotid",
    "OralCavity", "Lips", "Mandible", "Skin", "Esophagus",
})

_LATERALITY = ("_L", "_R")


def is_tg263_conformant(name: str) -> bool:
    """True if ``name`` follows the (bounded) TG-263 nomenclature convention."""
    if not name or name != name.strip() or re.search(r"\s", name):
        return False  # TG-263 forbids whitespace in structure names
    if _TARGET_RE.match(name):
        return True
    base = name[:-2] if name.endswith(_LATERALITY) else name
    return base in _TG263_OAR_BASES


def nonconformant_rois(names: Iterable[str]) -> List[str]:
    """Sorted unique ROI names that do not follow the TG-263 convention."""
    return sorted({n for n in names if not is_tg263_conformant(n)})
