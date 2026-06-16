from __future__ import annotations

from boundary_analyzer.pipeline.step_01_collect_traces import main as collect_traces
from boundary_analyzer.pipeline.step_02_read_traces import main as read_traces
from boundary_analyzer.pipeline.step_03_find_endpoints import main as find_endpoints
from boundary_analyzer.pipeline.step_04_find_db_tables import main as find_db_tables
from boundary_analyzer.pipeline.step_05_build_mapping import main as build_mapping
from boundary_analyzer.pipeline.step_06_compute_scom import main as compute_scom
from boundary_analyzer.pipeline.step_07_rank_and_flag import main as rank_and_flag
from boundary_analyzer.pipeline.step_08_make_report import main as make_report

__all__ = [
    "collect_traces",
    "read_traces",
    "find_endpoints",
    "find_db_tables",
    "build_mapping",
    "compute_scom",
    "rank_and_flag",
    "make_report",
]
