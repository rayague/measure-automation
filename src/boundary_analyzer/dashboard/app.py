"""
dashboard.py – Microservice Boundary Analyzer
==============================================
Dark-tech precision dashboard.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import dash
import pandas as pd
import plotly.graph_objects as go

from boundary_analyzer.auto.run_registry import REPORT_FILE as _REPORT_FILE
from boundary_analyzer.dashboard.charts import create_summary_cards
from boundary_analyzer.dashboard.design_tokens import (
    GLOBAL_CSS,
    PLOT_LAYOUT,
    T,
    _with_alpha,
)

logger = logging.getLogger(__name__)


def _load_service_rank_from(base_dir: Path) -> pd.DataFrame:
    paths = [
        base_dir / "processed" / "service_rank.csv",
        base_dir / "service_rank.csv",
    ]
    for path in paths:
        if path.exists():
            return pd.read_csv(path)
    return pd.DataFrame()


def _load_endpoint_table_map_from(base_dir: Path) -> pd.DataFrame:
    paths = [
        base_dir / "interim" / "endpoint_table_map.csv",
        base_dir / "endpoint_table_map.csv",  # fallback: some runs save it at root
    ]
    for p in paths:
        if p.exists():
            try:
                return pd.read_csv(p)
            except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError):
                continue
    return pd.DataFrame()


def _load_all(base_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rank_df_local = _load_service_rank_from(base_dir)
    mapping_df_local = _load_endpoint_table_map_from(base_dir)
    summary_local = create_summary_cards(rank_df_local)

    try:
        resolved_base_dir = base_dir.resolve()
    except OSError:
        resolved_base_dir = base_dir

    avg_scom_dbg = summary_local.get("avg_scom", 0.0)
    logger.info("[Dashboard] Loading data from: %s", resolved_base_dir)
    logger.info("[Dashboard] service_rank.csv rows: %s | avg_scom: %s", len(rank_df_local), avg_scom_dbg)
    if not rank_df_local.empty and "scom_score" in rank_df_local.columns:
        try:
            logger.info(
                "[Dashboard] scom_score min/max: %s/%s",
                float(rank_df_local["scom_score"].min()),
                float(rank_df_local["scom_score"].max()),
            )
        except (ValueError, TypeError) as e:
            logger.warning("[Dashboard] Could not compute scom_score min/max: %s", e)

    return rank_df_local, mapping_df_local, summary_local


def _load_llm_analysis(data_dir: Path | None = None) -> str | None:
    candidates = []
    if data_dir:
        candidates.append(data_dir / "report.md")
        candidates.append(data_dir / _REPORT_FILE)
    candidates.append(Path("reports/latest/report.md"))
    for path in candidates:
        if path and path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                marker = "## AI-Powered Analysis"
                if marker in content:
                    section = content.split(marker, 1)[1].strip()
                    next_h1 = section.find("\n# ")
                    if next_h1 != -1:
                        section = section[:next_h1]
                    if section.strip():
                        return section.strip()
            except OSError as e:
                logger.warning("[Dashboard] Could not read %s: %s", path, e)
    return None


def _load_trends(base_dir: Path, max_runs: int = 10) -> pd.DataFrame:
    """Load SCOM scores across recent runs for trend visualisation.

    Returns a DataFrame with:
      - index: service name
      - columns: run timestamps (YYYY-MM-DD HH:MM)
      - values: SCOM score per service per run
    """
    from boundary_analyzer.auto.run_registry import list_runs, load_run_meta

    # Infer data root from base_dir, defaulting to "data"
    data_root_guess = base_dir.parent.parent if base_dir.parent.name == "runs" else Path("data")
    all_runs = list_runs(data_root=data_root_guess)[:max_runs]
    if not all_runs:
        return pd.DataFrame()

    # Gather SCOM per run
    records: list[dict] = []
    for r in reversed(all_runs):
        meta = load_run_meta(r["id"], data_root_guess)
        if not meta:
            continue
        ts = meta.get("timestamp", "")[:16].replace("T", " ")
        for s in meta.get("scom_results", []):
            name = s.get("Service") or s.get("service") or "?"
            scom_val = s.get("SCOM") or s.get("scom") or 0.0
            try:
                records.append({"service": name, "run_ts": ts, "scom": float(scom_val)})
            except (ValueError, TypeError):
                continue

    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    return df.pivot_table(index="service", columns="run_ts", values="scom", aggfunc="first")


def _build_trend_chart(trend_df: pd.DataFrame) -> go.Figure:
    """Build a multi-line trend chart showing SCOM per service over runs."""
    fig = go.Figure()
    colors = [T["cyan"], T["amber"], T["red"], T["green"], "#bf7ff5", "#00d4aa", "#ff8c42", "#e040fb"]
    for i, svc in enumerate(trend_df.index):
        fig.add_trace(
            go.Scatter(
                x=trend_df.columns,
                y=trend_df.loc[svc],
                mode="lines+markers",
                name=svc,
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=6),
            )
        )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=T["font_mono"], color=T["text_secondary"], size=10),
        xaxis=dict(
            showgrid=True,
            gridcolor=T["border"],
            tickangle=-30,
            title="",
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor=T["border"],
            range=[0, 1],
            title="SCOM",
            tickformat=".2f",
        ),
        legend=dict(
            orientation="h",
            y=-0.3,
            font=dict(size=9, color=T["text_muted"]),
        ),
        margin=dict(l=40, r=20, t=10, b=80),
        hovermode="x unified",
    )
    return fig


def _get_data_freshness(base_dir: Path) -> str:
    candidates = [
        base_dir / "processed" / "service_rank.csv",
        base_dir / "service_rank.csv",
        base_dir / "meta.json",
    ]
    for path in candidates:
        try:
            if path.exists():
                mtime = path.stat().st_mtime
                dt = datetime.fromtimestamp(mtime)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass
    return "unknown"


# ── Chart builders ─────────────────────────────────────────────────────────


def _build_bar_chart(rank_df: pd.DataFrame) -> go.Figure:
    if rank_df.empty:
        return go.Figure()

    df = rank_df.sort_values("scom_score", ascending=True).copy()
    colors = [T["red"] if s else T["cyan"] for s in df["is_suspicious"]]
    df["status_label"] = df["is_suspicious"].map({True: "suspicious", False: "healthy"})

    fig = go.Figure(
        go.Bar(
            x=df["scom_score"],
            y=df["service_name"],
            orientation="h",
            marker=dict(
                color=colors,
                opacity=0.85,
                line=dict(width=0),
            ),
            text=df["scom_score"].map(lambda v: f"{v:.4f}"),
            textposition="outside",
            textfont=dict(family="JetBrains Mono", size=10, color=T["text_secondary"]),
            customdata=df[["rank", "endpoints_count", "tables_count", "status_label"]],
            hovertemplate=(
                f"<b>%{{y}}</b><br>"
                f"<span style='color:{T['cyan']}'>SCOM</span>: %{{x:.4f}}<br>"
                f"Rank: #%{{customdata[0]}}<br>"
                f"Endpoints: %{{customdata[1]}}<br>"
                f"Tables: %{{customdata[2]}}<br>"
                f"Status: %{{customdata[3]}}"
                f"<extra></extra>"
            ),
        )
    )

    thresh = None
    if "threshold_value" in rank_df.columns:
        thresh = rank_df["threshold_value"].iloc[0]
    elif "threshold" in rank_df.columns:
        thresh = rank_df["threshold"].iloc[0]

    if thresh is not None:
        fig.add_vline(
            x=float(thresh),
            line=dict(color=T["amber"], width=1.5, dash="dot"),
            annotation_text=f"threshold {float(thresh):.3f}",
            annotation_font=dict(color=T["amber"], size=9, family="JetBrains Mono"),
            annotation_position="top right",
        )

    fig.update_layout(
        {
            **PLOT_LAYOUT,
            "height": max(240, len(df) * 44),
            "showlegend": False,
            "xaxis": dict(**PLOT_LAYOUT["xaxis"], title="SCOM Score", range=[0, 1.08]),
            "yaxis": dict(**PLOT_LAYOUT["yaxis"], title=None, tickfont=dict(size=11)),
            "bargap": 0.28,
        }
    )
    return fig


def _build_distribution(rank_df: pd.DataFrame) -> go.Figure:
    if rank_df.empty:
        return go.Figure()

    healthy_df = rank_df[~rank_df["is_suspicious"]][["service_name", "scom_score"]]
    suspect_df = rank_df[rank_df["is_suspicious"]][["service_name", "scom_score"]]

    fig = go.Figure()
    for subdf, name, col in [
        (healthy_df, "Healthy", T["cyan"]),
        (suspect_df, "Suspicious", T["red"]),
    ]:
        if subdf.empty:
            continue
        fig.add_trace(
            go.Violin(
                y=subdf["scom_score"],
                name=name,
                box_visible=True,
                meanline_visible=True,
                fillcolor=_with_alpha(col, 0.12),
                line_color=col,
                points="all",
                pointpos=0,
                marker=dict(color=col, size=7, opacity=0.7),
                hovertext=subdf["service_name"],
                hovertemplate="<b>%{hovertext}</b><br>SCOM: %{y:.4f}<extra>" + name + "</extra>",
                hoveron="points",
            )
        )

    fig.update_layout(
        {
            **PLOT_LAYOUT,
            "height": 300,
            "violingap": 0.3,
            "violinmode": "group",
            "yaxis": dict(**PLOT_LAYOUT["yaxis"], title="SCOM Score", range=[-0.05, 1.05]),
            "xaxis": dict(**PLOT_LAYOUT["xaxis"], title=None),
        }
    )
    return fig


def _build_radar_chart(row: pd.Series) -> go.Figure:
    cats = ["SCOM Score", "Endpoint Density", "Table Diversity", "Cohesion Rank", "Health Index"]

    max_endpoints = 20
    max_tables = 15
    total_svcs = 10

    r_vals = [
        float(row.get("scom_score", 0)),
        min(float(row.get("endpoints_count", 0)) / max_endpoints, 1),
        min(float(row.get("tables_count", 0)) / max_tables, 1),
        1 - (float(row.get("rank", 1)) - 1) / max(total_svcs - 1, 1),
        0.0 if row.get("is_suspicious") else 1.0,
    ]

    raw_vals = [
        float(row.get("scom_score", 0)),
        int(row.get("endpoints_count", 0)),
        int(row.get("tables_count", 0)),
        int(row.get("rank", 1)),
        0 if row.get("is_suspicious") else 1,
    ]

    color = T["red"] if row.get("is_suspicious") else T["cyan"]

    fig = go.Figure(
        go.Scatterpolar(
            r=r_vals + [r_vals[0]],
            theta=cats + [cats[0]],
            fill="toself",
            fillcolor=_with_alpha(color, 0.13),
            line=dict(color=color, width=2),
            marker=dict(color=color, size=6),
            customdata=raw_vals + [raw_vals[0]],
            hovertemplate=("<b>%{theta}</b><br>Normalized: %{r:.3f}<br>Raw: %{customdata}<extra></extra>"),
        )
    )
    fig.update_layout(
        **PLOT_LAYOUT,
        height=300,
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(
                visible=True,
                range=[0, 1],
                gridcolor="rgba(0,229,255,0.08)",
                linecolor="rgba(0,229,255,0.12)",
                tickfont=dict(size=8, color=T["text_muted"]),
            ),
            angularaxis=dict(
                gridcolor="rgba(0,229,255,0.08)",
                linecolor="rgba(0,229,255,0.12)",
                tickfont=dict(size=9, family="JetBrains Mono"),
            ),
        ),
        showlegend=False,
    )
    return fig


def _build_heatmap(mapping_df: pd.DataFrame, service_name: str) -> go.Figure:
    if mapping_df.empty or "service_name" not in mapping_df.columns:
        return go.Figure()

    service_df = mapping_df[mapping_df["service_name"] == service_name]
    if service_df.empty:
        return go.Figure()

    if "endpoint_key" not in service_df.columns or "table" not in service_df.columns:
        return go.Figure()

    if "count" not in service_df.columns:
        service_df = service_df.copy()
        service_df["count"] = 1

    pivot = service_df.pivot_table(
        index="endpoint_key",
        columns="table",
        values="count",
        aggfunc="sum",
        fill_value=0,
    )

    cscale = [
        [0.0, "rgba(6,8,15,1)"],
        [0.25, "rgba(0,80,120,1)"],
        [0.6, "rgba(0,180,210,1)"],
        [1.0, "rgba(0,229,255,1)"],
    ]

    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=pivot.columns.tolist(),
            y=pivot.index.tolist(),
            colorscale=cscale,
            showscale=True,
            colorbar=dict(
                thickness=10,
                tickfont=dict(family="JetBrains Mono", size=9, color=T["text_secondary"]),
                outlinewidth=0,
            ),
            hovertemplate="Endpoint: %{y}<br>Table: %{x}<br>Calls: %{z}<extra></extra>",
        )
    )
    fig.update_layout(
        {
            **PLOT_LAYOUT,
            "height": max(260, len(pivot) * 36 + 80),
            "xaxis": dict(**PLOT_LAYOUT["xaxis"], tickangle=40, tickfont=dict(size=10)),
            "yaxis": dict(**PLOT_LAYOUT["yaxis"], tickfont=dict(size=10)),
            "margin": dict(t=16, b=60, l=180, r=24),
        }
    )
    return fig


# ── App factory ────────────────────────────────────────────────────────────


def create_app(data_dir: Path | None = None) -> dash.Dash:
    from boundary_analyzer.auto.run_registry import list_runs
    from boundary_analyzer.dashboard.callbacks import register_callbacks
    from boundary_analyzer.dashboard.layout_components import serve_layout

    app = dash.Dash(
        __name__,
        # Needed because layout components (dcc.Location, dcc.Store) are
        # created dynamically by serve_layout() — callbacks reference them
        # before any page has rendered.
        suppress_callback_exceptions=True,
        external_stylesheets=[
            "https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@300;400;600&display=swap",
        ],
    )

    app.index_string = f"""<!DOCTYPE html>
<html>
    <head>
        {{%metas%}}
        <title>{{%title%}}</title>
        {{%favicon%}}
        {{%css%}}
        <style>{GLOBAL_CSS}</style>
    </head>
    <body>
        {{%app_entry%}}
        <footer>
            {{%config%}}
            {{%scripts%}}
            {{%renderer%}}
        </footer>
    </body>
</html>"""

    # Initial run info (from CLI --run flag) — used to seed the dropdown + first load
    cli_data_dir = data_dir or Path("data")
    initial_run_id = ""
    runs_meta = list_runs(data_root=cli_data_dir)
    for r in runs_meta:
        rp = _resolve_run_path(r.get("id", ""))
        if rp and str(rp.resolve()) == str(cli_data_dir.resolve()):
            initial_run_id = r.get("id", "")
            break

    app.layout = lambda: serve_layout(
        cli_data_dir,
        run_id=initial_run_id,
        all_runs=runs_meta,
        cli_data_dir_str=str(cli_data_dir.resolve()),
    )
    register_callbacks(app)
    return app


def _resolve_run_path(run_id: str) -> Path | None:
    from boundary_analyzer.auto.run_registry import get_run_path

    return get_run_path(run_id)


# ── Entry point ────────────────────────────────────────────────────────────


def main(data_dir: Path | None = None) -> int:
    app = create_app(data_dir=data_dir)

    host = os.environ.get("BOUNDARY_ANALYZER_DASH_HOST", "127.0.0.1")
    port = int(os.environ.get("BOUNDARY_ANALYZER_DASH_PORT", "8050"))

    logger.info("\n  ** Boundary Analyzer -- http://%s:%s\n", host, port)
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
