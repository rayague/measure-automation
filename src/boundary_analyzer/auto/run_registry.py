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

#: Environment variable that pins the registry location explicitly.
DATA_DIR_ENV_VAR = "MBA_DATA_DIR"


def resolve_data_root() -> Path:
    """Resolve where the run registry lives, independent of the current directory.

    Historically every registry function defaulted to the *relative* path
    ``data/`` — i.e. relative to whatever directory the user happened to run
    the command from. Runs saved by ``mba full`` inside project A's folder
    were then invisible to ``mba dashboard`` or ``mba runs list`` launched
    from project B or from anywhere else, which made results look like they
    had silently vanished.

    Resolution order:

    1. ``MBA_DATA_DIR`` environment variable, when set — explicit override.
    2. ``./data`` when it already contains a registry index
       (``data/runs/runs.json``) — backwards compatibility: existing local
       registries keep working when you're inside those directories.
    3. A per-user central directory otherwise, so that runs land in ONE
       place no matter where the command is launched from:
       ``%LOCALAPPDATA%/boundary_analyzer/data`` on Windows,
       ``~/.local/share/boundary_analyzer/data`` elsewhere.
    """
    env_dir = os.environ.get(DATA_DIR_ENV_VAR, "").strip()
    if env_dir:
        return Path(env_dir)

    local = Path("data")
    if (local / RUNS_DIR_NAME / RUNS_INDEX_FILE).exists():
        return local

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "boundary_analyzer" / "data"
    return Path.home() / ".local" / "share" / "boundary_analyzer" / "data"


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
    for svc in project.services:
        services_list.append(
            {
                "name": svc.name,
                "language": svc.language,
                "framework": svc.framework,
                "endpoints": [e.key() for e in svc.endpoints],
                "tables": [],
            }
        )

    # Header totals must come from the SAME source as the per-service SCOM
    # table shown by `mba runs show` (the trace-measured counts in scom_list),
    # otherwise the summary line contradicts the rows right below it. The
    # previous implementation mixed AST-discovered endpoint counts (a
    # pre-deployment estimate) with trace counts, and summed per-service
    # table counts — double-counting every table shared between services.
    endpoints_total = 0
    for rec in scom_list:
        try:
            endpoints_total += int(rec.get("Endpoints") or rec.get("endpoints") or 0)
        except (ValueError, TypeError):
            pass
    if endpoints_total == 0:
        # No SCOM rows at all (e.g. failed run) — fall back to AST discovery.
        endpoints_total = sum(len(svc.endpoints) for svc in project.services)

    # Distinct tables across the whole run, from the endpoint→table mapping
    # when available (exact); otherwise the max per-service count is the best
    # non-double-counting lower bound we can report.
    tables_total = 0
    mapping_df = scom_results_raw.get("mapping_df")
    if mapping_df is not None and hasattr(mapping_df, "columns") and "table" in getattr(mapping_df, "columns", []):
        try:
            tables_total = int(mapping_df["table"].nunique())
        except (ValueError, TypeError, KeyError):
            tables_total = 0
    if tables_total == 0:
        for rec in scom_list:
            tables_val = rec.get("Tables")
            if tables_val is None:
                tables_val = rec.get("tables")
            if tables_val is None:
                tables_val = rec.get("Tables/Collections", 0)
            try:
                tables_total = max(tables_total, int(tables_val))
            except (ValueError, TypeError):
                pass

    traffic_step = report.step("traffic")
    traffic_data = traffic_step.data if traffic_step else {}
    if isinstance(traffic_data, dict):
        all_req = sum(getattr(t, "total_requests", 0) if not isinstance(t, dict) else t.get("total_requests", 0) for t in traffic_data.values())
        all_ok = sum(getattr(t, "successful_requests", 0) if not isinstance(t, dict) else t.get("successful_requests", 0) for t in traffic_data.values())
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
    data_root: Path | None = None,
) -> RunMeta:
    """Save an AnalysisReport to the run registry and return its metadata."""
    if data_root is None:
        data_root = resolve_data_root()
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

    # Explicitly save mapping_df to interim/endpoint_table_map.csv so the dashboard heatmap works.
    # This is the reliable path — independent of temp-dir copy which can fail on Windows.
    mapping_df = sources.get("mapping_df")
    if mapping_df is not None and hasattr(mapping_df, "to_csv") and not mapping_df.empty:
        try:
            interim_dir = run_dir / "interim"
            interim_dir.mkdir(parents=True, exist_ok=True)
            mapping_df.to_csv(interim_dir / "endpoint_table_map.csv", index=False)
        except Exception as e:
            logger.warning("Failed to save endpoint_table_map.csv: %s", e)

    saved_report_path = run_dir / REPORT_FILE
    original_report = report.report_path
    if original_report and Path(original_report).exists():
        try:
            shutil.copy2(original_report, saved_report_path)
        except OSError as e:
            logger.warning("Failed to copy report: %s", e)
            saved_report_path = Path("")

        temp_dir = Path(original_report).parent
        interim_src = temp_dir / "interim"
        if interim_src.exists():
            try:
                shutil.copytree(interim_src, run_dir / "interim", dirs_exist_ok=True)
            except Exception as e:
                logger.warning("Failed to copy interim directory to registry: %s", e)

        processed_src = temp_dir / "processed"
        if processed_src.exists():
            try:
                shutil.copytree(processed_src, run_dir / "processed", dirs_exist_ok=True)
            except Exception as e:
                logger.warning("Failed to copy processed directory to registry: %s", e)
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


def list_runs(data_root: Path | None = None) -> list[dict[str, Any]]:
    """List all saved runs, newest first."""
    if data_root is None:
        data_root = resolve_data_root()
    runs_dir = _ensure_runs_dir(data_root)
    index = _read_runs_index(runs_dir)
    return list(reversed(index))


def get_run_path(run_id: str, data_root: Path | None = None) -> Path | None:
    """Return the Path to a run's directory, or None if not found."""
    if data_root is None:
        data_root = resolve_data_root()
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


def load_run_meta(run_id: str, data_root: Path | None = None) -> dict[str, Any] | None:
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


def load_run_csv(run_id: str, csv_name: str, data_root: Path | None = None) -> str | None:
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


def get_last_run(data_root: Path | None = None) -> dict[str, Any] | None:
    """Get the most recent run's metadata."""
    if data_root is None:
        data_root = resolve_data_root()
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


def delete_run(run_id: str, data_root: Path | None = None) -> bool:
    """Delete a run's directory and remove it from the index."""
    if data_root is None:
        data_root = resolve_data_root()
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
