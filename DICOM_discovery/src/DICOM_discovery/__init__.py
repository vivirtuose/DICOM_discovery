"""DICOM_discovery — cohort-level QC for longitudinal radiotherapy (DICOM) data.

Architecture: a single ``indexer`` turns any file tree into a canonical table; the
analyses (RT chain integrity, longitudinal completeness) consume only that table and
never touch the filesystem, which is what makes the package adaptive to arbitrary
server structures.
"""
from .completeness import (  # noqa: F401
    DEFAULT_PROTOCOL,
    CellState,
    Protocol,
    assign_timepoints,
    build_completeness,
    load_protocol,
    patient_completeness,
)
from .indexer import IndexResult, build_index, read_instance  # noqa: F401
from .report_map import render_completeness_map  # noqa: F401
from .rt_integrity import (  # noqa: F401
    Confidence,
    Finding,
    RTObject,
    ScanResult,
    Severity,
    Status,
    assess_patient,
    build_rt_integrity,
    build_rt_integrity_from_dir,
    build_rt_rollup,
    order_for_review,
    read_rt_object,
    rtobjects_from_table,
    scan_rt_cohort,
)
from .synthetic import (  # noqa: F401
    GroundTruth,
    generate_longitudinal_cohort,
    generate_synthetic_cohort,
)

__version__ = "0.8.0"
