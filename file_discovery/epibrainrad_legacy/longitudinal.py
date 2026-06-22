# -*- coding: utf-8 -*-
"""Outils longitudinaux : timepoints, ordre d'affichage, parsing des dossiers."""
from __future__ import annotations
import re
from pathlib import Path

TIMEPOINT_ORDER = {
    "LB": 0,
    "M3": 3,
    "M4": 4,
    "M6": 6,
    "M9": 9,
    "M10": 10,
    "M12": 12,
    "M18": 18,
    "M24": 24,
    "M36": 36,
    "PRECHIR": 900,
    "POSTCHIR": 901,
    "UNKNOWN": 999,
}

TIMEPOINT_DISPLAY_ORDER = ["LB", "M3", "M4", "M6", "M9", "M10", "M12", "M18", "M24", "M36", "PRECHIR", "POSTCHIR", "UNKNOWN"]


def detect_timepoint_from_path(path: str | Path) -> str:
    """Détecte LB/Mx/PRECHIR/POSTCHIR à partir du chemin dossier."""
    text = str(path).upper().replace("-", "_").replace(" ", "_")
    # Priorité chirurgie pour éviter faux M dans certains noms
    if re.search(r"PRE[_]*CHIR|PRECHIR|PREOP|PRE[_]*OP", text):
        return "PRECHIR"
    if re.search(r"POST[_]*CHIR|POSTCHIR|POSTOP|POST[_]*OP", text):
        return "POSTCHIR"
    if re.search(r"(^|[_/])LB($|[_/])", text):
        return "LB"
    # M suivis d'un nombre entier, ex M4, M06, M12
    matches = re.findall(r"(^|[_/])M0?([0-9]{1,2})($|[_/])", text)
    if matches:
        months = [int(m[1]) for m in matches]
        if months:
            return f"M{months[0]}"
    return "UNKNOWN"


def timepoint_sort_key(tp: str) -> int:
    return TIMEPOINT_ORDER.get(str(tp).upper(), 998)
