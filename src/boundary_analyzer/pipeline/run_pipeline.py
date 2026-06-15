from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from boundary_analyzer._utils import save_csv
from boundary_analyzer.detection.db_table_extractor import extract_db_operations
from boundary_analyzer.detection.endpoint_extractor import extract_endpoints
from boundary_analyzer.detection.mapping_builder import build_endpoint_table_mapping
from boundary_analyzer.metrics.scom import compute_scom
from boundary_analyzer.metrics.threshold_ultimate import apply_threshold
from boundary_analyzer.parsing.trace_reader import read_all_traces, save_spans_csv
from boundary_analyzer.reporting.report_builder import generate_report


def _prepare_traces(input_path: Path, traces_dir: Path) -> None:
    traces_dir.mkdir(parents=True, exist_ok=True)

    # If input IS traces_dir, files are already in place — skip copy
    if input_path.resolve() == traces_dir.resolve():
        return

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
    scom_method: str = "weighted",
    table_weighting: bool = True,
    endpoint_weighting: bool = True,
    threshold_method: str = "percentile",
    threshold_percentile: float = 25.0,
    threshold_zscore: float = -1.5,
    fixed_threshold: float = 0.5,
    exclude_services: list[str] | None = None,
    exclude_health_routes: bool = True,
    exclude_http_client_spans: bool = True,
    exclude_unknown_endpoint: bool = True,
    skip_no_db_services: bool = False,
) -> int:
    raw_traces_dir = output_dir / "raw" / "traces"
    interim_dir = output_dir / "interim"
    processed_dir = output_dir / "processed"

    _prepare_traces(traces, raw_traces_dir)

    spans_df = read_all_traces(raw_traces_dir)
    spans_csv = interim_dir / "spans.csv"
    save_spans_csv(spans_df, spans_csv)

    endpoints_df = extract_endpoints(
        spans_df,
        exclude_health_routes=exclude_health_routes,
        exclude_http_client_spans=exclude_http_client_spans,
    )
    endpoints_csv = interim_dir / "endpoints.csv"
    save_csv(endpoints_df, endpoints_csv)

    db_ops_df = extract_db_operations(spans_df)
    db_ops_csv = interim_dir / "db_operations.csv"
    save_csv(db_ops_df, db_ops_csv)

    mapping_df = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)
    mapping_csv = interim_dir / "endpoint_table_map.csv"
    save_csv(mapping_df, mapping_csv)

    scom_kwargs: dict[str, Any] = {
        "exclude_services": exclude_services,
        "exclude_unknown_endpoint": exclude_unknown_endpoint,
        "skip_no_db_services": skip_no_db_services,
    }
    if scom_method == "paper":
        scom_kwargs["use_endpoint_weighting"] = True
    elif scom_method == "weighted":
        scom_kwargs["use_endpoint_weighting"] = endpoint_weighting
    else:
        scom_kwargs["use_endpoint_weighting"] = False
    scom_df = compute_scom(mapping_df, endpoints_df=endpoints_df, **scom_kwargs)
    service_scom_csv = processed_dir / "service_scom.csv"
    save_csv(scom_df, service_scom_csv)

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
        rank_df = pd.DataFrame(
            columns=[
                "service_name",
                "scom_score",
                "endpoints_count",
                "tables_count",
                "method",
                "rank",
                "threshold_value",
                "threshold_method",
                "is_suspicious",
            ]
        )

    service_rank_csv = processed_dir / "service_rank.csv"
    save_csv(rank_df, service_rank_csv)

    suspicious_df = rank_df[rank_df.get("is_suspicious", False)].copy()
    suspicious_csv = processed_dir / "suspicious_services.csv"
    save_csv(suspicious_df, suspicious_csv)

    report_path = output_dir / "report.md"
    generate_report(service_rank_csv, suspicious_csv, report_path, fixed_threshold)

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Boundary Analyzer pipeline on a traces file/folder")
    ap.add_argument("--traces", required=True, help="Path to a .json traces file or a folder containing JSON files")
    ap.add_argument("--service", default="", help="Service name (reserved, kept for compatibility)")
    ap.add_argument("--output", required=True, help="Output folder")

    ap.add_argument("--scom-method", default="weighted", choices=["paper", "weighted", "simple"])
    ap.add_argument("--table-weighting", action="store_true", default=True)
    ap.add_argument("--no-table-weighting", action="store_false", dest="table_weighting")
    ap.add_argument("--endpoint-weighting", action="store_true", default=True)
    ap.add_argument("--no-endpoint-weighting", action="store_false", dest="endpoint_weighting")

    ap.add_argument("--threshold-method", default="percentile", choices=["percentile", "zscore", "fixed"])
    ap.add_argument("--threshold-percentile", type=float, default=25.0)
    ap.add_argument("--threshold-zscore", type=float, default=-1.5)
    ap.add_argument("--fixed-threshold", type=float, default=0.5)

    ap.add_argument("--exclude-services", nargs="*", default=None, help="Service names to exclude from analysis (e.g. gateway)")
    ap.add_argument(
        "--no-exclude-health",
        action="store_false",
        dest="exclude_health_routes",
        help="Do NOT filter out health/infrastructure endpoints",
    )
    ap.add_argument(
        "--no-exclude-http-client",
        action="store_false",
        dest="exclude_http_client_spans",
        help="Do NOT filter out HTTP client spans (http send/receive)",
    )
    ap.add_argument(
        "--no-exclude-unknown-endpoint",
        action="store_false",
        dest="exclude_unknown_endpoint",
        help="Do NOT filter out unknown_endpoint entries from SCOM computation",
    )
    ap.add_argument(
        "--skip-no-db-services",
        action="store_true",
        default=False,
        help="Exclude services with no DB tables detected from SCOM ranking",
    )

    args = ap.parse_args(argv)

    traces_path = Path(args.traces)
    output_dir = Path(args.output)

    return run_pipeline(
        traces=traces_path,
        output_dir=output_dir,
        scom_method=str(args.scom_method),
        table_weighting=bool(args.table_weighting),
        endpoint_weighting=bool(args.endpoint_weighting),
        threshold_method=args.threshold_method,
        threshold_percentile=args.threshold_percentile,
        threshold_zscore=args.threshold_zscore,
        fixed_threshold=args.fixed_threshold,
        exclude_services=args.exclude_services,
        exclude_health_routes=bool(args.exclude_health_routes),
        exclude_http_client_spans=bool(args.exclude_http_client_spans),
        exclude_unknown_endpoint=bool(args.exclude_unknown_endpoint),
        skip_no_db_services=bool(args.skip_no_db_services),
    )


if __name__ == "__main__":
    raise SystemExit(main())
