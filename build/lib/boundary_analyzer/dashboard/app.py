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

from boundary_analyzer.dashboard.charts import create_summary_cards
from boundary_analyzer.dashboard.design_tokens import (
    GLOBAL_CSS,
    PLOT_LAYOUT,
    T,
    _with_alpha,
)

logger = logging.getLogger(__name__)


def _load_service_rank_from(base_dir: Path) -> pd.DataFrame:
    path = base_dir / "processed" / "service_rank.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _load_endpoint_table_map_from(base_dir: Path) -> pd.DataFrame:
    path = base_dir / "interim" / "endpoint_table_map.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


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


def _load_llm_analysis() -> str | None:
    path = Path("reports/latest/report.md")
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        marker = "## AI-Powered Analysis"
        if marker in content:
            section = content.split(marker, 1)[1].strip()
            next_h1 = section.find("\n# ")
            if next_h1 != -1:
                section = section[:next_h1]
            return section if section.strip() else None
    except OSError as e:
        logger.warning("[Dashboard] Could not read %s: %s", path, e)
        return None
    return None


def _get_data_freshness(base_dir: Path) -> str:
    rank_path = base_dir / "processed" / "service_rank.csv"
    try:
        if rank_path.exists():
            mtime = rank_path.stat().st_mtime
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
    service_df = mapping_df[mapping_df["service_name"] == service_name]
    if service_df.empty:
        return go.Figure()

    pivot = service_df.pivot_table(index="endpoint_key", columns="table", values="count", fill_value=0)

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
    from boundary_analyzer.dashboard.callbacks import register_callbacks
    from boundary_analyzer.dashboard.layout_components import serve_layout

    app = dash.Dash(
        __name__,
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

    base_dir = data_dir or Path("data")
    app.layout = lambda: serve_layout(base_dir)
    register_callbacks(app, base_dir)
    return app


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
