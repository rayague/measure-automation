from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("boundary_analyzer")


RUNS_DIR_NAME = "runs"
RUNS_INDEX_FILE = "runs.json"
META_FILE = "meta.json"
REPORT_FILE = "report.md"
SCOM_CSV = "service_scom.csv"
RANK_CSV = "service_rank.csv"
SUSPICIOUS_CSV = "suspicious_services.csv"
LAST_RUN_FILE = "last_run.txt"


@dataclass
class RunMeta:
    id: str
    timestamp: str
    project_name: str
    project_root: str
    language: str
    services: list[dict[str, Any]]
    endpoints_total: int
    tables_total: int
    traffic_requests: int
    traffic_ok: int
    traffic_failed: int
    scom_results: list[dict[str, Any]]
    duration_seconds: float
    all_success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_path: str = ""
    boundaries_dir: str = ""


def _ensure_runs_dir(data_root: Path) -> Path:
    runs_dir = data_root / RUNS_DIR_NAME
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


def _read_runs_index(runs_dir: Path) -> list[dict[str, Any]]:
    index_file = runs_dir / RUNS_INDEX_FILE
    if index_file.exists():
        try:
            return json.loads(index_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupted runs.json (%s) — starting fresh index", exc)
            return []
    return []


def _write_runs_index(runs_dir: Path, index: list[dict[str, Any]]) -> None:
    index_file = runs_dir / RUNS_INDEX_FILE
    tmp = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(runs_dir), suffix=".tmp")
        tmp = Path(tmp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, default=str, ensure_ascii=False)
            f.flush()
            os.fsync(fd)
        os.replace(str(tmp), str(index_file))
    finally:
        if tmp and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _update_runs_index(runs_dir: Path, modifier: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> None:
    """Atomically read, modify, and write the runs index."""
    index = _read_runs_index(runs_dir)
    index = modifier(index)
    _write_runs_index(runs_dir, index)


def _generate_run_id(project_name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)
    return f"{ts}_{safe_name}"


def _build_run_meta(report: Any, run_id: str, report_path: Path, boundaries_dir: Path) -> RunMeta:
    project = report.project
    scom_results_raw = report.scom_results or {}

    scom_list: list[dict[str, Any]] = []
    scom_df = scom_results_raw.get("scom_df")
    if scom_df is not None and hasattr(scom_df, "to_dict"):
        try:
            scom_list = scom_df.to_dict(orient="records")
        except (ValueError, TypeError, AttributeError):
            pass
    elif isinstance(scom_df, list):
        scom_list = scom_df

    rank_list: list[dict[str, Any]] = []
    rank_df = scom_results_raw.get("rank_df")
    if rank_df is not None and hasattr(rank_df, "to_dict"):
        try:
            rank_list = rank_df.to_dict(orient="records")
        except (ValueError, TypeError, AttributeError):
            pass

    if not scom_list and rank_list:
        scom_list = rank_list

    # Normalize SCOM keys so all downstream consumers can rely on canonical names
    KEY_MAP = {"service_name": "Service", "scom_score": "SCOM", "endpoints_count": "Endpoints", "tables_count": "Tables"}
    for d in scom_list:
        for old_k, new_k in KEY_MAP.items():
            if old_k in d and new_k not in d:
                d[new_k] = d[old_k]

    services_list = []
    endpoints_total = 0
    tables_total = 0
    for svc in project.services:
        svc_info = {
            "name": svc.name,
            "language": svc.language,
            "framework": svc.framework,
            "endpoints": [e.key() for e in svc.endpoints],
            "tables": [],
        }
        services_list.append(svc_info)
        endpoints_total += len(svc.endpoints)

        for rec in scom_list:
            svc_name_rec = rec.get("Service") or rec.get("service") or ""
            if svc_name_rec == svc.name:
                ep_rec = rec.get("Endpoints") or rec.get("endpoints") or 0
                if endpoints_total > 0:
                    pass  # already counted from AST
                else:
                    try:
                        endpoints_total += int(ep_rec)
                    except (ValueError, TypeError):
                        pass
                tables_val = rec.get("Tables")
                if tables_val is None:
                    tables_val = rec.get("tables")
                if tables_val is None:
                    tables_val = rec.get("Tables/Collections", 0)
                try:
                    tables_total += int(tables_val)
                except (ValueError, TypeError):
                    pass
                break

    traffic_step = report.step("traffic")
    traffic_data = traffic_step.data if traffic_step else {}
    if isinstance(traffic_data, dict):
        all_req = sum(
            getattr(t, "total_requests", 0) if not isinstance(t, dict) else t.get("total_requests", 0)
            for t in traffic_data.values()
        )
        all_ok = sum(
            getattr(t, "successful_requests", 0) if not isinstance(t, dict) else t.get("successful_requests", 0)
            for t in traffic_data.values()
        )
    else:
        all_req = 0
        all_ok = 0

    errors_list = [str(e) for e in report.all_errors()]
    warnings_list = [str(w) for w in report.all_warnings()]

    return RunMeta(
        id=run_id,
        timestamp=datetime.now().isoformat(),
        project_name=project.name if hasattr(project, "name") else project.root_dir.name,
        project_root=str(project.root_dir.resolve()),
        language=project.language,
        services=services_list,
        endpoints_total=endpoints_total,
        tables_total=tables_total,
        traffic_requests=all_req,
        traffic_ok=all_ok,
        traffic_failed=all_req - all_ok,
        scom_results=scom_list,
        duration_seconds=report.total_duration_seconds,
        all_success=report.all_success,
        errors=errors_list,
        warnings=warnings_list,
        report_path=str(report_path),
        boundaries_dir=str(boundaries_dir),
    )


def save_run(
    report: Any,
    data_root: Path = Path("data"),
) -> RunMeta:
    """Save an AnalysisReport to the run registry and return its metadata."""
    runs_dir = _ensure_runs_dir(data_root)
    project_name = report.project.root_dir.name
    run_id = _generate_run_id(project_name)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sources = report.scom_results or {}

    csv_files = {
        SCOM_CSV: sources.get("scom_df"),
        RANK_CSV: sources.get("rank_df"),
        SUSPICIOUS_CSV: sources.get("suspicious_df"),
    }

    for csv_name, df in csv_files.items():
        if df is not None and hasattr(df, "to_csv"):
            try:
                csv_path = run_dir / csv_name
                df.to_csv(csv_path, index=False)
            except Exception as e:
                logger.warning("Failed to save %s: %s", csv_name, e)

    saved_report_path = run_dir / REPORT_FILE
    original_report = report.report_path
    if original_report and Path(original_report).exists():
        try:
            shutil.copy2(original_report, saved_report_path)
        except OSError as e:
            logger.warning("Failed to copy report: %s", e)
            saved_report_path = Path("")
    else:
        saved_report_path = Path("")

    meta = _build_run_meta(report, run_id, saved_report_path, run_dir)
    meta_obj = asdict(meta)
    meta_obj["report_path"] = str(saved_report_path)
    meta_obj["boundaries_dir"] = str(run_dir)

    meta_file = run_dir / META_FILE
    meta_file.write_text(
        json.dumps(meta_obj, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

    _update_runs_index(runs_dir, lambda idx: [e for e in idx if e.get("id") != run_id] + [meta_obj])

    last_run_file = runs_dir / LAST_RUN_FILE
    try:
        last_run_file.write_text(run_id, encoding="utf-8")
    except OSError:
        pass

    logger.info("Saved run %s to %s", run_id, run_dir)
    return meta


def list_runs(data_root: Path = Path("data")) -> list[dict[str, Any]]:
    """List all saved runs, newest first."""
    runs_dir = _ensure_runs_dir(data_root)
    index = _read_runs_index(runs_dir)
    return list(reversed(index))


def get_run_path(run_id: str, data_root: Path = Path("data")) -> Path | None:
    """Return the Path to a run's directory, or None if not found."""
    runs_dir = _ensure_runs_dir(data_root)
    candidates = []
    for p in runs_dir.glob(f"{run_id}*"):
        try:
            _ = p.stat()
            candidates.append(p)
        except OSError:
            continue
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    exact = runs_dir / run_id
    if exact.is_dir():
        return exact
    return None


def load_run_meta(run_id: str, data_root: Path = Path("data")) -> dict[str, Any] | None:
    """Load a run's metadata from its meta.json."""
    run_dir = get_run_path(run_id, data_root)
    if not run_dir:
        return None
    meta_file = run_dir / META_FILE
    if not meta_file.exists():
        return None
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load meta for run %s: %s", run_id, e)
        return None


def load_run_csv(run_id: str, csv_name: str, data_root: Path = Path("data")) -> str | None:
    """Load a CSV from a run's directory by name."""
    import pandas as pd

    run_dir = get_run_path(run_id, data_root)
    if not run_dir:
        return None
    csv_path = run_dir / csv_name
    if not csv_path.exists():
        return None
    try:
        df = pd.read_csv(csv_path)
        return df.to_csv(index=False)
    except (ValueError, OSError, pd.errors.EmptyDataError):
        return None


def get_last_run(data_root: Path = Path("data")) -> dict[str, Any] | None:
    """Get the most recent run's metadata."""
    runs_dir = _ensure_runs_dir(data_root)
    last_run_file = runs_dir / LAST_RUN_FILE
    if last_run_file.exists():
        run_id = last_run_file.read_text(encoding="utf-8").strip()
        meta = load_run_meta(run_id, data_root)
        if meta:
            return meta

    index = _read_runs_index(runs_dir)
    if index:
        last = index[-1]
        meta = load_run_meta(last.get("id", ""), data_root)
        if meta:
            return meta
    return None


def delete_run(run_id: str, data_root: Path = Path("data")) -> bool:
    """Delete a run's directory and remove it from the index."""
    runs_dir = _ensure_runs_dir(data_root)
    run_dir = get_run_path(run_id, data_root)
    if not run_dir:
        return False

    try:
        shutil.rmtree(run_dir)
    except OSError as e:
        logger.warning("Failed to delete run directory %s: %s", run_dir, e)
        return False

    _update_runs_index(runs_dir, lambda idx: [e for e in idx if e.get("id") != run_id])

    last_run_file = runs_dir / LAST_RUN_FILE
    if last_run_file.exists() and last_run_file.read_text(encoding="utf-8").strip() == run_id:
        try:
            last_run_file.unlink()
        except OSError:
            pass

    return True
