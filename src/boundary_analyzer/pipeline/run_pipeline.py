from __future__ import annotations

import argparse
import json
import logging
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
from boundary_analyzer.parsing.log_ingestion import SPANS_COLUMNS, ingest_log_file
from boundary_analyzer.parsing.trace_reader import save_spans_csv
from boundary_analyzer.reporting.report_builder import generate_report

logger = logging.getLogger(__name__)


def _iter_input_files(input_path: Path) -> list[Path]:
    """Return direct input files in deterministic order."""
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(p for p in input_path.iterdir() if p.is_file())
    raise FileNotFoundError(str(input_path))


def _prepare_traces(input_path: Path, traces_dir: Path) -> list[Path]:
    traces_dir.mkdir(parents=True, exist_ok=True)

    # If input IS traces_dir, files are already in place; skip copy.
    if input_path.resolve() == traces_dir.resolve():
        return _iter_input_files(traces_dir)

    if input_path.is_file():
        dest = traces_dir / input_path.name
        shutil.copy2(input_path, dest)
        return [dest]

    if input_path.is_dir():
        copied: list[Path] = []
        for p in _iter_input_files(input_path):
            dest = traces_dir / p.name
            shutil.copy2(p, dest)
            copied.append(dest)
        return copied

    raise FileNotFoundError(str(input_path))


def _empty_spans_df() -> pd.DataFrame:
    return pd.DataFrame(columns=SPANS_COLUMNS)


def _write_ingestion_summary(summary_path: Path, summary: dict[str, Any]) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def _read_universal_logs(
    input_files: list[Path],
    summary_path: Path,
    service_name: str = "",
    format_hint: str = "",
    encoding: str = "utf-8",
) -> pd.DataFrame:
    """Parse prepared trace/log files into the canonical spans DataFrame."""
    frames: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for file_path in input_files:
        try:
            result = ingest_log_file(
                file_path,
                service_name=service_name,
                format_hint=format_hint,
                encoding=encoding,
            )
        except Exception as exc:
            errors.append({"source": str(file_path), "error": str(exc)})
            logger.warning("Skipping unparsable log input %s: %s", file_path, exc)
            continue

        frames.append(result.spans_df)
        sources.append(
            {
                "source": str(file_path),
                "format": result.format_detected,
                "confidence": result.format_confidence,
                "service_name": result.service_name_used,
                "has_db_info": result.has_db_info,
                "has_trace_correlation": result.has_trace_correlation,
                "stats": result.stats,
                "warnings": result.warnings,
            }
        )

    duplicates_removed = 0
    if frames:
        spans_df = pd.concat(frames, ignore_index=True)
        spans_df = spans_df[SPANS_COLUMNS].copy()

        # Multiple input files can legitimately contain the same span more
        # than once — e.g. exporting Jaeger traces "per service" returns the
        # *entire* multi-service trace for every service that participated
        # in it, so a request touching 3 services is exported 3 times. Left
        # unchecked this silently inflates endpoint/table frequency counts
        # and skews the (frequency-weighted) SCOM score. Only dedupe rows
        # with a real, non-empty (trace_id, span_id) — synthetic IDs from
        # sources with no native span identity (nginx, w3c, locust, raw_text)
        # are always freshly generated per row and must never collide.
        has_real_id = (spans_df["trace_id"].astype(str).str.strip() != "") & (spans_df["span_id"].astype(str).str.strip() != "")
        identifiable = spans_df[has_real_id]
        dupe_mask = identifiable.duplicated(subset=["trace_id", "span_id"], keep="first")
        if dupe_mask.any():
            duplicates_removed = int(dupe_mask.sum())
            spans_df = spans_df.drop(index=identifiable.index[dupe_mask])
            logger.info(
                "Dropped %d duplicate span(s) (same trace_id/span_id repeated across input files)",
                duplicates_removed,
            )
    else:
        spans_df = _empty_spans_df()

    if spans_df.empty:
        total_spans = 0
        http_spans = 0
        db_spans = 0
        services: list[str] = []
        unique_traces = 0
        correlated_db_spans = 0
    else:
        tags_series = spans_df["tags"].fillna("").astype(str)
        total_spans = int(len(spans_df))
        http_spans = int(tags_series.str.contains("http.method", na=False, regex=False).sum())
        db_mask = tags_series.str.contains("db.system", na=False, regex=False) | tags_series.str.contains("db.statement", na=False, regex=False)
        for _key in ("db.query.text", "sql.query"):
            db_mask = db_mask | tags_series.str.contains(_key, na=False, regex=False)
        db_spans = int(db_mask.sum())
        services = sorted(spans_df["service_name"].dropna().astype(str).unique().tolist())
        unique_traces = int(spans_df["trace_id"].nunique())
        correlated_db_spans = int(spans_df.loc[db_mask, "parent_span_id"].notna().sum()) if db_spans else 0

    summary = {
        "totals": {
            "files_seen": len(input_files),
            "files_parsed": len(sources),
            "files_failed": len(errors),
            "total_spans": total_spans,
            "http_spans": http_spans,
            "db_spans": db_spans,
            "correlated_db_spans": correlated_db_spans,
            "services": services,
            "unique_traces": unique_traces,
            "duplicate_spans_removed": duplicates_removed,
        },
        "sources": sources,
        "errors": errors,
    }
    _write_ingestion_summary(summary_path, summary)

    if not frames and input_files:
        tried = ", ".join(str(p) for p in input_files)
        raise ValueError(f"No supported log/traces data could be parsed from: {tried}")

    return spans_df


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
    service_name: str = "",
    format_hint: str = "",
    encoding: str = "utf-8",
) -> int:
    raw_traces_dir = output_dir / "raw" / "traces"
    interim_dir = output_dir / "interim"
    processed_dir = output_dir / "processed"

    input_files = _prepare_traces(traces, raw_traces_dir)

    ingestion_summary_path = interim_dir / "ingestion_summary.json"
    spans_df = _read_universal_logs(
        input_files,
        ingestion_summary_path,
        service_name=service_name,
        format_hint=format_hint,
        encoding=encoding,
    )
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
    # "paper" reproduces the exact formula from Section III-C of the ICSA26 paper:
    # SCOM = sum_{i<j} CI(e_i,e_j) / (N * CI_max)
    # which is UNWEIGHTED (w_ij = 1 for all pairs).
    # The weighted variant (w_ij = freq(e_i) * freq(e_j)) is a pragmatic extension
    # described in Section IV-B and is the default of "weighted" / "mba full".
    if scom_method == "paper":
        scom_kwargs["use_endpoint_weighting"] = False  # exact paper formula (unweighted)
    elif scom_method == "weighted":
        scom_kwargs["use_endpoint_weighting"] = endpoint_weighting
    else:
        scom_kwargs["use_endpoint_weighting"] = False

    scom_df = compute_scom(mapping_df, endpoints_df=endpoints_df, **scom_kwargs)

    # When using the paper method, also compute the weighted variant and store it
    # as an extra column so the report can reproduce Table I of the paper (both columns).
    if scom_method == "paper" and not scom_df.empty:
        weighted_df = compute_scom(
            mapping_df,
            endpoints_df=endpoints_df,
            use_endpoint_weighting=True,
            exclude_services=exclude_services,
            exclude_unknown_endpoint=exclude_unknown_endpoint,
            skip_no_db_services=skip_no_db_services,
        )
        if not weighted_df.empty:
            merge_cols = ["service_name", "scom_score"]
            scom_df = scom_df.merge(
                weighted_df[["service_name", "scom_score"]].rename(columns={"scom_score": "scom_score_weighted"}),
                on="service_name",
                how="left",
            )
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
    generate_report(service_rank_csv, suspicious_csv, report_path, fixed_threshold, ingestion_summary_path=ingestion_summary_path)

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Boundary Analyzer pipeline on a traces file/folder")
    ap.add_argument("--traces", required=True, help="Path to a traces/log file or a folder containing trace/log files")
    ap.add_argument("--service", default="", help="Override service name for logs that do not contain one")
    ap.add_argument("--output", required=True, help="Output folder")
    ap.add_argument("--format", default="", help="Optional input format hint: jaeger, zipkin, otlp, locust, nginx, w3c, generic_sql, json_lines, raw_text")
    ap.add_argument("--encoding", default="utf-8", help="Preferred text encoding for log files (default: utf-8)")

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
        service_name=str(args.service),
        format_hint=str(args.format),
        encoding=str(args.encoding),
    )


if __name__ == "__main__":
    raise SystemExit(main())
