from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd

from boundary_analyzer.detection.db_table_extractor import extract_db_operations
from boundary_analyzer.detection.endpoint_extractor import extract_endpoints
from boundary_analyzer.detection.mapping_builder import build_endpoint_table_mapping
from boundary_analyzer.metrics.scom_ultimate import compute_weighted_scom
from boundary_analyzer.metrics.threshold_ultimate import apply_threshold
from boundary_analyzer.parsing.trace_reader import read_all_traces, save_spans_csv
from boundary_analyzer.reporting.report_builder import generate_report


def _prepare_traces(input_path: Path, traces_dir: Path) -> None:
    traces_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_file():
        if input_path.suffix.lower() != ".json":
            raise ValueError(f"Unsupported traces file: {input_path}")
        shutil.copy2(input_path, traces_dir / input_path.name)
        return

    if input_path.is_dir():
        for p in input_path.glob("*.json"):
            shutil.copy2(p, traces_dir / p.name)
        return

    raise FileNotFoundError(str(input_path))


def run_pipeline(
    traces: Path,
    output_dir: Path,
    threshold_method: str = "percentile",
    threshold_percentile: float = 25.0,
    threshold_zscore: float = -1.5,
    fixed_threshold: float = 0.5,
) -> int:
    raw_traces_dir = output_dir / "raw" / "traces"
    interim_dir = output_dir / "interim"
    processed_dir = output_dir / "processed"

    _prepare_traces(traces, raw_traces_dir)

    spans_df = read_all_traces(raw_traces_dir)
    spans_csv = interim_dir / "spans.csv"
    save_spans_csv(spans_df, spans_csv)

    endpoints_df = extract_endpoints(spans_df)
    endpoints_csv = interim_dir / "endpoints.csv"
    endpoints_csv.parent.mkdir(parents=True, exist_ok=True)
    endpoints_df.to_csv(endpoints_csv, index=False)

    db_ops_df = extract_db_operations(spans_df)
    db_ops_csv = interim_dir / "db_operations.csv"
    db_ops_csv.parent.mkdir(parents=True, exist_ok=True)
    db_ops_df.to_csv(db_ops_csv, index=False)

    mapping_df = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)
    mapping_csv = interim_dir / "endpoint_table_map.csv"
    mapping_csv.parent.mkdir(parents=True, exist_ok=True)
    mapping_df.to_csv(mapping_csv, index=False)

    scom_df = compute_weighted_scom(mapping_df)
    service_scom_csv = processed_dir / "service_scom.csv"
    service_scom_csv.parent.mkdir(parents=True, exist_ok=True)
    scom_df.to_csv(service_scom_csv, index=False)

    if not scom_df.empty:
        rank_df = scom_df.sort_values("scom_score").reset_index(drop=True)
        rank_df["rank"] = rank_df.index + 1
        rank_df = apply_threshold(
            rank_df,
            threshold_method=threshold_method,
            threshold_percentile=threshold_percentile,
            threshold_zscore=threshold_zscore,
            fixed_threshold=fixed_threshold,
        )
    else:
        rank_df = pd.DataFrame(columns=[
            "service_name",
            "scom_score",
            "endpoints_count",
            "tables_count",
            "method",
            "rank",
            "threshold_value",
            "threshold_method",
            "is_suspicious",
        ])

    service_rank_csv = processed_dir / "service_rank.csv"
    rank_df.to_csv(service_rank_csv, index=False)

    suspicious_df = rank_df[rank_df.get("is_suspicious", False) == True].copy()
    suspicious_csv = processed_dir / "suspicious_services.csv"
    suspicious_df.to_csv(suspicious_csv, index=False)

    report_path = output_dir / "report.md"
    generate_report(service_rank_csv, suspicious_csv, report_path, fixed_threshold)

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Boundary Analyzer pipeline on a traces file/folder")
    ap.add_argument("--traces", required=True, help="Path to a .json traces file or a folder containing JSON files")
    ap.add_argument("--service", default="", help="Service name (reserved, kept for compatibility)")
    ap.add_argument("--output", required=True, help="Output folder")

    ap.add_argument("--threshold-method", default="percentile", choices=["percentile", "zscore", "fixed"])
    ap.add_argument("--threshold-percentile", type=float, default=25.0)
    ap.add_argument("--threshold-zscore", type=float, default=-1.5)
    ap.add_argument("--fixed-threshold", type=float, default=0.5)

    args = ap.parse_args(argv)

    traces_path = Path(args.traces)
    output_dir = Path(args.output)

    return run_pipeline(
        traces=traces_path,
        output_dir=output_dir,
        threshold_method=args.threshold_method,
        threshold_percentile=args.threshold_percentile,
        threshold_zscore=args.threshold_zscore,
        fixed_threshold=args.fixed_threshold,
    )


if __name__ == "__main__":
    raise SystemExit(main())
