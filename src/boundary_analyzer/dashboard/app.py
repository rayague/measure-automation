"""
dashboard.py – Microservice Boundary Analyzer
==============================================
Dark-tech precision dashboard.
Design language: deep space + engineering cockpit.
Fonts: Syne (display) + JetBrains Mono (data).
Palette: #0a0e1a bg, #00e5ff cyan accent, #ff6d00 amber alert, #1e2a3a cards.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import dash_table, dcc, html
from dash.dependencies import Input, Output, State

from boundary_analyzer.dashboard.charts import (
    create_animated_bar_chart,
    create_scom_distribution,
    create_summary_cards,
)


def _with_alpha(color: str, alpha: float) -> str:
    """Return an rgba() color string with the requested alpha.

    Supports:
    - '#RRGGBB'
    - 'rgb(r,g,b)'
    - 'rgba(r,g,b,a)'
    """
    c = str(color).strip()
    if c.startswith("rgba("):
        # Replace alpha in existing rgba
        inner = c[len("rgba(") : -1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) >= 3:
            r, g, b = parts[:3]
            return f"rgba({r},{g},{b},{alpha})"
    if c.startswith("rgb("):
        inner = c[len("rgb(") : -1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 3:
            r, g, b = parts
            return f"rgba({r},{g},{b},{alpha})"
    if c.startswith("#") and len(c) == 7:
        r = int(c[1:3], 16)
        g = int(c[3:5], 16)
        b = int(c[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return c

# ─────────────────────────────────────────────────────────────────────────────
# DESIGN TOKENS – single source of truth for every colour and spacing
# ─────────────────────────────────────────────────────────────────────────────
T = {
    # Background layers
    "bg_base":    "#06080f",
    "bg_card":    "rgba(14, 22, 38, 0.85)",
    "bg_card2":   "rgba(18, 28, 50, 0.70)",
    "bg_header":  "rgba(6, 8, 15, 0.95)",

    # Accent colours
    "cyan":       "#00e5ff",
    "cyan_dim":   "rgba(0, 229, 255, 0.12)",
    "cyan_glow":  "rgba(0, 229, 255, 0.35)",
    "amber":      "#ff9800",
    "amber_dim":  "rgba(255, 152, 0, 0.12)",
    "amber_glow": "rgba(255, 152, 0, 0.35)",
    "green":      "#00e676",
    "green_dim":  "rgba(0, 230, 118, 0.10)",
    "red":        "#ff1744",
    "red_dim":    "rgba(255, 23, 68, 0.12)",

    # Text hierarchy
    "text_primary":   "#e8f0fe",
    "text_secondary": "rgba(200, 220, 255, 0.55)",
    "text_muted":     "rgba(200, 220, 255, 0.30)",

    # Borders & dividers
    "border":     "rgba(0, 229, 255, 0.10)",
    "border_hot": "rgba(0, 229, 255, 0.40)",

    # Fonts
    "font_display": "'Syne', 'DM Sans', sans-serif",
    "font_mono":    "'JetBrains Mono', 'Fira Code', monospace",
}

# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL CSS injected as a style tag
# ─────────────────────────────────────────────────────────────────────────────
GLOBAL_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@300;400;600&display=swap');

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --cyan:      {T['cyan']};
  --amber:     {T['amber']};
  --green:     {T['green']};
  --red:       {T['red']};
  --bg:        {T['bg_base']};
  --card:      {T['bg_card']};
  --border:    {T['border']};
  --text:      {T['text_primary']};
  --text2:     {T['text_secondary']};
}}

html, body {{
  background: {T['bg_base']};
  color: {T['text_primary']};
  font-family: {T['font_display']};
  min-height: 100vh;
  overflow-x: hidden;
}}

/* Scrollbar */
::-webkit-scrollbar {{ width: 6px; background: {T['bg_base']}; }}
::-webkit-scrollbar-thumb {{ background: {T['border_hot']}; border-radius: 3px; }}

/* Grid noise texture overlay */
body::before {{
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(0,229,255,0.015) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,229,255,0.015) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events: none;
  z-index: 0;
}}

/* Ambient glow orbs */
body::after {{
  content: '';
  position: fixed;
  top: -30vh;
  left: -10vw;
  width: 70vw;
  height: 70vh;
  background: radial-gradient(ellipse, rgba(0,150,255,0.06) 0%, transparent 65%);
  pointer-events: none;
  z-index: 0;
}}

/* Card base */
.dash-card {{
  position: relative;
  background: {T['bg_card']};
  border: 1px solid {T['border']};
  border-radius: 16px;
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  overflow: hidden;
  transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.3s ease;
}}
.dash-card:hover {{
  border-color: {T['border_hot']};
  box-shadow: 0 0 40px {T['cyan_glow']}, 0 20px 60px rgba(0,0,0,0.4);
  transform: translateY(-2px);
}}
.dash-card::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, {T['cyan']}, transparent);
  opacity: 0.5;
}}

/* KPI metric card */
.metric-card {{
  position: relative;
  background: {T['bg_card']};
  border: 1px solid {T['border']};
  border-radius: 14px;
  padding: 24px 28px;
  backdrop-filter: blur(18px);
  overflow: hidden;
  transition: all 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
  cursor: default;
  flex: 1;
  min-width: 160px;
}}
.metric-card:hover {{
  transform: translateY(-4px) scale(1.02);
  box-shadow: 0 12px 40px rgba(0,0,0,0.4);
}}
.metric-card--cyan {{ border-color: {T['cyan_glow']}; }}
.metric-card--cyan::after {{
  content: '';
  position: absolute;
  bottom: -30px; right: -30px;
  width: 120px; height: 120px;
  background: radial-gradient(circle, {T['cyan_dim']}, transparent 70%);
  pointer-events: none;
}}
.metric-card--amber {{ border-color: {T['amber_glow']}; }}
.metric-card--amber::after {{
  content: '';
  position: absolute;
  bottom: -30px; right: -30px;
  width: 120px; height: 120px;
  background: radial-gradient(circle, {T['amber_dim']}, transparent 70%);
  pointer-events: none;
}}
.metric-card--green {{ border-color: rgba(0,230,118,0.35); }}
.metric-card--green::after {{
  content: '';
  position: absolute;
  bottom: -30px; right: -30px;
  width: 120px; height: 120px;
  background: radial-gradient(circle, {T['green_dim']}, transparent 70%);
  pointer-events: none;
}}

/* Section label above cards */
.section-label {{
  font-family: {T['font_mono']};
  font-size: 10px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: {T['text_muted']};
  margin-bottom: 14px;
}}

/* Animated underline accent */
.title-accent {{
  display: inline-block;
  position: relative;
}}
.title-accent::after {{
  content: '';
  position: absolute;
  bottom: -4px; left: 0;
  width: 100%; height: 2px;
  background: linear-gradient(90deg, {T['cyan']}, transparent);
}}

/* Pulse dot for suspicious status */
@keyframes pulse-ring {{
  0%   {{ box-shadow: 0 0 0 0 rgba(255,23,68,0.6); }}
  70%  {{ box-shadow: 0 0 0 10px rgba(255,23,68,0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(255,23,68,0); }}
}}
.pulse-dot {{
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: {T['red']};
  animation: pulse-ring 1.8s infinite;
  margin-right: 8px;
  vertical-align: middle;
}}

/* Fade-in for page transitions */
@keyframes fadeSlideUp {{
  from {{ opacity: 0; transform: translateY(16px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}
.fade-in {{
  animation: fadeSlideUp 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
}}

/* Table overrides */
.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner td {{
  font-family: {T['font_mono']} !important;
  font-size: 12.5px !important;
  border-bottom: 1px solid {T['border']} !important;
  background: transparent !important;
  color: {T['text_primary']} !important;
  padding: 13px 16px !important;
  transition: background 0.15s;
}}
.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner tr:hover td {{
  background: rgba(0,229,255,0.04) !important;
}}
.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner th {{
  font-family: {T['font_mono']} !important;
  font-size: 10px !important;
  letter-spacing: 2px !important;
  text-transform: uppercase !important;
  background: rgba(0,229,255,0.05) !important;
  color: {T['cyan']} !important;
  border-bottom: 1px solid {T['border_hot']} !important;
  padding: 12px 16px !important;
}}
.dash-table-container {{
  border: 1px solid {T['border']};
  border-radius: 12px;
  overflow: hidden;
}}

/* Back button */
.back-btn {{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: {T['font_mono']};
  font-size: 12px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: {T['cyan']};
  background: {T['cyan_dim']};
  border: 1px solid {T['cyan_glow']};
  padding: 10px 20px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}}
.back-btn:hover {{
  background: {T['cyan']};
  color: {T['bg_base']};
  box-shadow: 0 0 20px {T['cyan_glow']};
}}

/* Header scan line animation */
@keyframes scan {{
  0%   {{ top: -4px; }}
  100% {{ top: 100%; }}
}}
"""

# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY THEME – applied to every chart
# ─────────────────────────────────────────────────────────────────────────────
PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="JetBrains Mono, monospace", color=T["text_primary"], size=11),
    colorway=[T["cyan"], T["amber"], T["green"], "#7c4dff", "#f06292", "#80cbc4"],
    hovermode="closest",
    xaxis=dict(
        gridcolor="rgba(0,229,255,0.06)",
        linecolor="rgba(0,229,255,0.12)",
        tickcolor="rgba(0,229,255,0.25)",
        zerolinecolor="rgba(0,229,255,0.08)",
    ),
    yaxis=dict(
        gridcolor="rgba(0,229,255,0.06)",
        linecolor="rgba(0,229,255,0.12)",
        tickcolor="rgba(0,229,255,0.25)",
        zerolinecolor="rgba(0,229,255,0.08)",
    ),
    hoverlabel=dict(
        bgcolor="rgba(14,22,38,0.97)",
        bordercolor=T["cyan"],
        font_family="JetBrains Mono, monospace",
        font_color=T["text_primary"],
        font_size=12,
    ),
    margin=dict(t=40, b=40, l=40, r=24),
    legend=dict(
        bgcolor="rgba(14,22,38,0.7)",
        bordercolor=T["border"],
        borderwidth=1,
        font=dict(family="JetBrains Mono", size=11),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def _load_service_rank() -> pd.DataFrame:
    path = Path("data/processed/service_rank.csv")
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _load_endpoint_table_map() -> pd.DataFrame:
    path = Path("data/interim/endpoint_table_map.csv")
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


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

# ─────────────────────────────────────────────────────────────────────────────
# CHART BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_bar_chart(rank_df: pd.DataFrame) -> go.Figure:
    """Horizontal bar chart with cyan/amber colour coding by health status."""
    if rank_df.empty:
        return go.Figure()

    df = rank_df.sort_values("scom_score", ascending=True).copy()
    colors = [T["red"] if s else T["cyan"] for s in df["is_suspicious"]]
    df["status_label"] = df["is_suspicious"].map({True: "suspicious", False: "healthy"})

    fig = go.Figure(go.Bar(
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
    ))

    # Threshold line
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

    fig.update_layout({
        **PLOT_LAYOUT,
        "height": max(240, len(df) * 44),
        "showlegend": False,
        "xaxis": dict(**PLOT_LAYOUT["xaxis"], title="SCOM Score", range=[0, 1.08]),
        "yaxis": dict(**PLOT_LAYOUT["yaxis"], title=None, tickfont=dict(size=11)),
        "bargap": 0.28,
    })
    return fig


def _build_distribution(rank_df: pd.DataFrame) -> go.Figure:
    """Violin + strip chart for SCOM distribution with healthy/suspicious split."""
    if rank_df.empty:
        return go.Figure()

    healthy_df = rank_df[~rank_df["is_suspicious"]][["service_name", "scom_score"]]
    suspect_df = rank_df[rank_df["is_suspicious"]][["service_name", "scom_score"]]

    fig = go.Figure()
    for subdf, name, col in [
        (healthy_df, "Healthy",    T["cyan"]),
        (suspect_df, "Suspicious", T["red"]),
    ]:
        if subdf.empty:
            continue
        fig.add_trace(go.Violin(
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
        ))

    fig.update_layout({
        **PLOT_LAYOUT,
        "height": 300,
        "violingap": 0.3,
        "violinmode": "group",
        "yaxis": dict(**PLOT_LAYOUT["yaxis"], title="SCOM Score", range=[-0.05, 1.05]),
        "xaxis": dict(**PLOT_LAYOUT["xaxis"], title=None),
    })
    return fig


def _build_radar_chart(row: pd.Series) -> go.Figure:
    """Radar / spider chart for single service multi-metric view."""
    cats = ["SCOM Score", "Endpoint Density", "Table Diversity",
            "Cohesion Rank", "Health Index"]

    max_endpoints = 20
    max_tables    = 15
    total_svcs    = 10  # approximate; replaced dynamically when available

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

    fig = go.Figure(go.Scatterpolar(
        r=r_vals + [r_vals[0]],
        theta=cats + [cats[0]],
        fill="toself",
        fillcolor=_with_alpha(color, 0.13),
        line=dict(color=color, width=2),
        marker=dict(color=color, size=6),
        customdata=raw_vals + [raw_vals[0]],
        hovertemplate=(
            "<b>%{theta}</b><br>"
            "Normalized: %{r:.3f}<br>"
            "Raw: %{customdata}"
            "<extra></extra>"
        ),
    ))
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
    """Endpoint × Table access heatmap with custom dark colour scale."""
    service_df = mapping_df[mapping_df["service_name"] == service_name]
    if service_df.empty:
        return go.Figure()

    pivot = service_df.pivot_table(
        index="endpoint_key", columns="table", values="count", fill_value=0
    )

    # Custom colour scale: near-black → deep cyan
    cscale = [
        [0.0,  "rgba(6,8,15,1)"],
        [0.25, "rgba(0,80,120,1)"],
        [0.6,  "rgba(0,180,210,1)"],
        [1.0,  "rgba(0,229,255,1)"],
    ]

    fig = go.Figure(go.Heatmap(
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
    ))
    fig.update_layout({
        **PLOT_LAYOUT,
        "height": max(260, len(pivot) * 36 + 80),
        "xaxis": dict(**PLOT_LAYOUT["xaxis"], tickangle=40, tickfont=dict(size=10)),
        "yaxis": dict(**PLOT_LAYOUT["yaxis"], tickfont=dict(size=10)),
        "margin": dict(t=16, b=60, l=180, r=24),
    })
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# UI COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────

def _metric_card(label: str, value, variant: str = "cyan") -> html.Div:
    """
    KPI metric card.
    variant = 'cyan' | 'amber' | 'green' | 'red'
    """
    val_color = {
        "cyan":  T["cyan"],
        "amber": T["amber"],
        "green": T["green"],
        "red":   T["red"],
    }.get(variant, T["cyan"])

    return html.Div(
        className=f"metric-card metric-card--{variant}",
        children=[
            html.P(label, style={
                "fontFamily": T["font_mono"],
                "fontSize":   "9px",
                "letterSpacing": "3px",
                "textTransform": "uppercase",
                "color": T["text_muted"],
                "marginBottom": "14px",
            }),
            html.P(str(value), style={
                "fontFamily": T["font_mono"],
                "fontSize":   "34px",
                "fontWeight": "600",
                "color":      val_color,
                "lineHeight": "1",
                "letterSpacing": "-1px",
            }),
        ],
    )


def _card(title: str, children, style_extra=None) -> html.Div:
    """Glassmorphic card with section title."""
    return html.Div(
        className="dash-card fade-in",
        style={
            "padding": "28px 32px",
            "marginBottom": "20px",
            **(style_extra or {}),
        },
        children=[
            html.P(title, className="section-label"),
            *children,
        ],
    )


def _status_badge(is_suspicious: bool) -> html.Span:
    if is_suspicious:
        return html.Span([
            html.Span(className="pulse-dot"),
            "SUSPICIOUS",
        ], style={
            "fontFamily":  T["font_mono"],
            "fontSize":    "10px",
            "letterSpacing": "2px",
            "color":       T["red"],
            "background":  T["red_dim"],
            "border":      f"1px solid {T['red']}44",
            "padding":     "5px 12px",
            "borderRadius": "4px",
        })
    return html.Span("● HEALTHY", style={
        "fontFamily":  T["font_mono"],
        "fontSize":    "10px",
        "letterSpacing": "2px",
        "color":       T["green"],
        "background":  T["green_dim"],
        "border":      f"1px solid {T['green']}44",
        "padding":     "5px 12px",
        "borderRadius": "4px",
    })


def _build_table(df: pd.DataFrame) -> dash_table.DataTable:
    """Dark-themed interactive DataTable."""
    if df.empty:
        return dash_table.DataTable()

    disp = df[["rank", "service_name", "scom_score",
               "endpoints_count", "tables_count", "is_suspicious"]].copy()
    disp["is_suspicious"] = disp["is_suspicious"].map({True: "⚠ suspect", False: "✓ healthy"})

    return dash_table.DataTable(
        id="service-table",
        data=disp.to_dict("records"),
        columns=[
            {"name": "#",         "id": "rank"},
            {"name": "Service",   "id": "service_name"},
            {"name": "SCOM",      "id": "scom_score", "type": "numeric",
             "format": {"specifier": ".4f"}},
            {"name": "Endpoints", "id": "endpoints_count", "type": "numeric"},
            {"name": "Tables",    "id": "tables_count",    "type": "numeric"},
            {"name": "Status",    "id": "is_suspicious"},
        ],
        style_header={
            "backgroundColor": "transparent",
            "borderBottom":    f"1px solid {T['border_hot']}",
        },
        style_cell={
            "backgroundColor": "transparent",
            "color":           T["text_primary"],
            "border":          "none",
        },
        style_data_conditional=[
            {
                "if": {"filter_query": '{is_suspicious} contains "suspect"'},
                "color":      T["red"],
                "fontWeight": "600",
            },
            {
                "if": {"filter_query": '{is_suspicious} contains "healthy"'},
                "color":      T["green"],
            },
            {
                "if": {"column_id": "rank"},
                "color":      T["text_muted"],
                "textAlign":  "center",
            },
            {
                "if": {"column_id": "scom_score"},
                "color":      T["cyan"],
                "textAlign":  "right",
            },
            {
                "if": {"state": "selected"},
                "backgroundColor": T["cyan_dim"],
                "border":          f"1px solid {T['cyan_glow']}",
            },
        ],
        page_size=8,
        row_selectable="single",
        style_as_list_view=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# PAGE LAYOUTS
# ─────────────────────────────────────────────────────────────────────────────

def _overview_layout(rank_df: pd.DataFrame, summary: dict) -> html.Div:
    total    = summary.get("total_services", 0)
    suspect  = summary.get("suspicious_count", 0)
    healthy  = summary.get("safe_count", 0)
    avg_scom = summary.get("avg_scom", 0.0)

    return html.Div(
        className="fade-in",
        children=[
            # ── KPI row ───────────────────────────────────────────────────────
            html.Div(
                style={"display": "flex", "gap": "14px", "marginBottom": "24px", "flexWrap": "wrap"},
                children=[
                    _metric_card("Total Services",  total,            "cyan"),
                    _metric_card("Suspicious",       suspect,          "red"),
                    _metric_card("Healthy",          healthy,          "green"),
                    _metric_card("Avg SCOM",         f"{avg_scom:.3f}","amber"),
                ],
            ),

            # ── Distribution ──────────────────────────────────────────────────
            _card("SCOM Score Distribution — healthy vs suspicious", [
                dcc.Graph(
                    figure=_build_distribution(rank_df),
                    config={"displayModeBar": False},
                ),
            ]),

            # ── Bar chart ─────────────────────────────────────────────────────
            _card("Service Cohesion Ranking", [
                dcc.Graph(
                    figure=_build_bar_chart(rank_df),
                    config={"displayModeBar": False},
                ),
            ]),

            # ── Table ─────────────────────────────────────────────────────────
            _card("All Services — click a row to inspect", [
                _build_table(rank_df),
                html.P(
                    "Click any row to open the service detail view.",
                    style={
                        "fontFamily": T["font_mono"],
                        "fontSize":   "10px",
                        "color":      T["text_muted"],
                        "marginTop":  "12px",
                        "letterSpacing": "1px",
                    },
                ),
            ]),

            _card("Definitions — how to read these charts", [
                _definitions_block(rank_df),
            ], style_extra={"marginBottom": "0"}),
        ],
    )


def _definitions_block(rank_df: pd.DataFrame) -> html.Div:
    """Glossary for metrics/terms used in the dashboard."""

    threshold_value = None
    threshold_method = None
    if not rank_df.empty:
        if "threshold_value" in rank_df.columns:
            threshold_value = float(rank_df["threshold_value"].iloc[0])
        elif "threshold" in rank_df.columns:
            threshold_value = float(rank_df["threshold"].iloc[0])

        if "threshold_method" in rank_df.columns:
            threshold_method = str(rank_df["threshold_method"].iloc[0])

    def _term_row(term: str, meaning: str) -> html.Div:
        return html.Div(
            style={"marginBottom": "10px", "lineHeight": "1.55"},
            children=[
                html.Span(term + ": ", style={
                    "fontFamily": T["font_mono"],
                    "fontSize": "12px",
                    "color": T["cyan"],
                }),
                html.Span(meaning, style={
                    "fontFamily": T["font_display"],
                    "fontSize": "12.5px",
                    "color": T["text_secondary"],
                }),
            ],
        )

    thresh_txt = (
        f"{threshold_value:.4f}" if threshold_value is not None else "(not available)"
    )
    method_txt = (
        f"method={threshold_method}" if threshold_method else "method is configured in settings.yaml"
    )

    return html.Div(
        children=[
            _term_row(
                "SCOM (Service Cohesion Score)",
                "A 0–1 cohesion score per service computed from endpoint→table access overlap. 1.0 means endpoints touch highly-overlapping tables (high cohesion); lower values mean endpoints access more disjoint table sets (potential Wrong Cut).",
            ),
            _term_row(
                "Threshold / Seuil",
                f"Services with SCOM < threshold are flagged suspicious. Current threshold={thresh_txt} ({method_txt}).",
            ),
            _term_row(
                "Healthy vs Suspicious",
                "Healthy means SCOM ≥ threshold. Suspicious means SCOM < threshold (service likely mixes multiple domains).",
            ),
            _term_row(
                "Rank",
                "Ordering by SCOM (ascending): rank #1 is the lowest cohesion (worst), larger rank is better cohesion.",
            ),
            _term_row(
                "Endpoints",
                "Number of distinct HTTP endpoints observed in traces for the service (method + normalized route).",
            ),
            _term_row(
                "Tables",
                "Number of distinct database tables extracted from DB spans (from SQL statements like SELECT/INSERT/UPDATE/DELETE).",
            ),
            _term_row(
                "Distribution (violin)",
                "Shows the spread of SCOM scores across services; each point corresponds to one service (hover to see its name and score).",
            ),
            _term_row(
                "Heatmap (detail view)",
                "Rows=endpoint, columns=table, value=calls (how often this endpoint accessed that table).",
            ),
            _term_row(
                "Radar (detail view)",
                "A normalized 0–1 view of multiple indicators. ‘Raw’ values appear in hover; normalization uses caps (endpoints≤20, tables≤15) and rank scaling.",
            ),
        ]
    )


def _detail_layout(
    service_name: str,
    rank_df: pd.DataFrame,
    mapping_df: pd.DataFrame,
) -> html.Div:
    svc = rank_df[rank_df["service_name"] == service_name]
    if svc.empty:
        return html.Div("Service not found.")

    row = svc.iloc[0]
    is_suspicious = bool(row["is_suspicious"])
    accent = T["red"] if is_suspicious else T["cyan"]

    explanation = (
        "Endpoints within this service access disjoint sets of database tables. "
        "This pattern suggests the service may be doing the work of two or more "
        "independent domains — a classic Wrong Cut. Consider splitting it."
        if is_suspicious
        else "Endpoints within this service access a tightly overlapping set of database tables. "
        "This indicates strong cohesion: the service is responsible for one well-defined domain."
    )

    return html.Div(
        className="fade-in",
        children=[
            # ── Status banner ─────────────────────────────────────────────────
            html.Div(
                style={
                    "background": T["red_dim"] if is_suspicious else T["green_dim"],
                    "border": f"1px solid {T['red']}33" if is_suspicious else f"1px solid {T['green']}33",
                    "borderLeft": f"3px solid {accent}",
                    "borderRadius": "10px",
                    "padding": "18px 24px",
                    "marginBottom": "24px",
                    "display": "flex",
                    "alignItems": "flex-start",
                    "gap": "14px",
                },
                children=[
                    _status_badge(is_suspicious),
                    html.P(
                        explanation,
                        style={
                            "fontFamily": T["font_display"],
                            "fontSize": "13px",
                            "color": T["text_secondary"],
                            "lineHeight": "1.65",
                            "margin": "0",
                        },
                    ),
                ],
            ),

            # ── KPI row ───────────────────────────────────────────────────────
            html.Div(
                style={"display": "flex", "gap": "14px", "marginBottom": "24px", "flexWrap": "wrap"},
                children=[
                    _metric_card(
                        "SCOM Score",
                        f"{row['scom_score']:.4f}",
                        "amber" if is_suspicious else "cyan",
                    ),
                    _metric_card("Rank", f"#{row['rank']}", "cyan"),
                    _metric_card("Endpoints", row["endpoints_count"], "green"),
                    _metric_card("Tables", row["tables_count"], "cyan"),
                ],
            ),

            # ── Two-column: heatmap + radar ───────────────────────────────────
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "1fr 340px",
                    "gap": "20px",
                    "marginBottom": "20px",
                },
                children=[
                    _card(
                        "Endpoint × Table Access Heatmap",
                        [
                            dcc.Graph(
                                figure=_build_heatmap(mapping_df, service_name),
                                config={"displayModeBar": False},
                            ),
                        ],
                        style_extra={"marginBottom": "0"},
                    ),
                    _card(
                        "Multi-Metric Radar",
                        [
                            dcc.Graph(
                                figure=_build_radar_chart(row),
                                config={"displayModeBar": False},
                            ),
                        ],
                        style_extra={"marginBottom": "0"},
                    ),
                ],
            ),
        ],
    )

# ─────────────────────────────────────────────────────────────────────────────
# APP FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def create_app(data_dir: Optional[Path] = None) -> dash.Dash:
    app = dash.Dash(
        __name__,
        suppress_callback_exceptions=True,
        # Load Google Fonts via external stylesheet
        external_stylesheets=[
            "https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800"
            "&family=JetBrains+Mono:wght@300;400;600&display=swap",
        ],
    )

    # Inject global CSS in a version-compatible way (some Dash versions lack html.Style)
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

    def _load_all() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        rank_df_local = _load_service_rank_from(base_dir)
        mapping_df_local = _load_endpoint_table_map_from(base_dir)
        summary_local = create_summary_cards(rank_df_local)

        try:
            resolved_base_dir = base_dir.resolve()
        except OSError:
            resolved_base_dir = base_dir

        avg_scom_dbg = summary_local.get("avg_scom", 0.0)
        print(f"[Dashboard] Loading data from: {resolved_base_dir}")
        print(f"[Dashboard] service_rank.csv rows: {len(rank_df_local)} | avg_scom: {avg_scom_dbg}")
        if not rank_df_local.empty and "scom_score" in rank_df_local.columns:
            try:
                print(
                    f"[Dashboard] scom_score min/max: {float(rank_df_local['scom_score'].min())}/{float(rank_df_local['scom_score'].max())}"
                )
            except Exception:
                print("[Dashboard] Could not compute scom_score min/max")

        return rank_df_local, mapping_df_local, summary_local

    def _build_data_warning(rank_df_local: pd.DataFrame, mapping_df_local: pd.DataFrame):
        missing_inputs: list[str] = []
        if rank_df_local.empty:
            missing_inputs.append(str(base_dir / "processed" / "service_rank.csv"))

        data_warning_local = None
        if missing_inputs:
            data_warning_local = html.Div(
                style={
                    "border": f"1px solid {T['border_hot']}",
                    "background": T["bg_card"],
                    "padding": "14px 16px",
                    "borderRadius": "10px",
                    "marginBottom": "18px",
                },
                children=[
                    html.Div("No data found", style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "12px",
                        "color": T["text_primary"],
                        "marginBottom": "6px",
                    }),
                    html.Div(
                        "I cannot show charts because input CSV files are missing or empty.",
                        style={"color": T["text_secondary"], "fontSize": "12px"},
                    ),
                    html.Div(
                        "Run: boundary-analyzer run --skip-collect (or run full pipeline).",
                        style={"color": T["text_muted"], "fontSize": "12px", "marginTop": "8px"},
                    ),
                ],
            )
        elif mapping_df_local.empty:
            data_warning_local = html.Div(
                style={
                    "border": f"1px solid {T['amber']}",
                    "background": T["bg_card"],
                    "padding": "14px 16px",
                    "borderRadius": "10px",
                    "marginBottom": "18px",
                },
                children=[
                    html.Div("ℹ️ HTTP-only mode", style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "12px",
                        "color": T["amber"],
                        "marginBottom": "6px",
                    }),
                    html.Div(
                        "No database operations found in traces. Showing HTTP endpoints only.",
                        style={"color": T["text_secondary"], "fontSize": "12px"},
                    ),
                    html.Div(
                        "Tip: Ensure your service has DB activity and SQLAlchemy instrumentation.",
                        style={"color": T["text_muted"], "fontSize": "12px", "marginTop": "8px"},
                    ),
                ],
            )

        return data_warning_local

    def _get_data_freshness() -> str:
        """Return a human-readable freshness string from the most recent data file."""
        rank_path = base_dir / "processed" / "service_rank.csv"
        try:
            if rank_path.exists():
                mtime = rank_path.stat().st_mtime
                dt = datetime.fromtimestamp(mtime)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass
        return "unknown"

    def _serve_layout():
        rank_df, mapping_df, summary = _load_all()
        data_warning = _build_data_warning(rank_df, mapping_df)
        data_freshness = _get_data_freshness()
        try:
            data_source_label = str(base_dir.resolve())
        except OSError:
            data_source_label = str(base_dir)
        return html.Div(
            style={"minHeight": "100vh", "background": T["bg_base"], "position": "relative"},
            children=[
                html.Div(id="reload-dummy", style={"display": "none"}),
                dcc.Location(id="url", refresh=False),
                # ── Header ────────────────────────────────────────────────────
                html.Header(
                    style={
                        "position":        "sticky",
                        "top":             "0",
                        "zIndex":          "100",
                        "background":      T["bg_header"],
                        "borderBottom":    f"1px solid {T['border']}",
                        "backdropFilter":  "blur(20px)",
                        "padding":         "0 40px",
                        "height":          "64px",
                        "display":         "flex",
                        "alignItems":      "center",
                        "justifyContent":  "space-between",
                    },
                    children=[
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "16px"},
                            children=[
                                # Logo glyph
                                html.Div("◈", style={
                                    "fontSize":   "22px",
                                    "color":      T["cyan"],
                                    "lineHeight": "1",
                                }),
                                html.Div([
                                    html.Span("Boundary", style={
                                        "fontFamily":    T["font_display"],
                                        "fontWeight":    "800",
                                        "fontSize":      "15px",
                                        "color":         T["text_primary"],
                                        "letterSpacing": "-0.3px",
                                    }),
                                    html.Span(" Analyzer", style={
                                        "fontFamily":    T["font_display"],
                                        "fontWeight":    "400",
                                        "fontSize":      "15px",
                                        "color":         T["text_secondary"],
                                    }),
                                ]),
                                html.Span("SCOM v2", style={
                                    "fontFamily":    T["font_mono"],
                                    "fontSize":      "9px",
                                    "letterSpacing": "2px",
                                    "color":         T["cyan"],
                                    "background":    T["cyan_dim"],
                                    "border":        f"1px solid {T['border_hot']}",
                                    "padding":       "3px 8px",
                                    "borderRadius":  "4px",
                                }),
                            ],
                        ),
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "16px"},
                            children=[
                                html.P("Microservice Cohesion Intelligence", style={
                                    "fontFamily":    T["font_mono"],
                                    "fontSize":      "10px",
                                    "letterSpacing": "2px",
                                    "color":         T["text_muted"],
                                    "textTransform": "uppercase",
                                    "margin":        "0",
                                }),
                                html.Button(
                                    "Reload data",
                                    id="reload-button",
                                    n_clicks=0,
                                    className="back-btn",
                                ),
                            ],
                        ),
                    ],
                ),

                # ── Data source indicator ─────────────────────────────────────
                html.Div(
                    style={
                        "background":     T["bg_card2"],
                        "borderBottom":   f"1px solid {T['border']}",
                        "padding":        "8px 40px",
                        "display":        "flex",
                        "alignItems":     "center",
                        "justifyContent": "space-between",
                        "gap":            "12px",
                    },
                    children=[
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "8px"},
                            children=[
                                html.Span("DATA SOURCE", style={
                                    "fontFamily":    T["font_mono"],
                                    "fontSize":      "8px",
                                    "letterSpacing": "2px",
                                    "color":         T["text_muted"],
                                }),
                                html.Span(data_source_label, style={
                                    "fontFamily":    T["font_mono"],
                                    "fontSize":      "10px",
                                    "color":         T["cyan"],
                                }),
                            ],
                        ),
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "8px"},
                            children=[
                                html.Span("LAST UPDATED", style={
                                    "fontFamily":    T["font_mono"],
                                    "fontSize":      "8px",
                                    "letterSpacing": "2px",
                                    "color":         T["text_muted"],
                                }),
                                html.Span(data_freshness, style={
                                    "fontFamily":    T["font_mono"],
                                    "fontSize":      "10px",
                                    "color":         T["amber"],
                                }),
                            ],
                        ),
                    ],
                ),

                # ── Main content ──────────────────────────────────────────────
                html.Main(
                    style={
                        "padding":    "32px 40px",
                        "maxWidth":   "1400px",
                        "margin":     "0 auto",
                        "position":   "relative",
                        "zIndex":     "1",
                    },
                    children=[
                        dcc.Store(id="selected-service", data=None),

                        data_warning,

                        # Overview page
                        html.Div(
                            id="overview-page",
                            children=_overview_layout(rank_df, summary),
                        ),

                        # Detail page (hidden until a service is clicked)
                        html.Div(
                            id="detail-page",
                            style={"display": "none"},
                            children=[
                                # Fixed header so callback Inputs always exist
                                html.Div(
                                    style={
                                        "display": "flex",
                                        "alignItems": "center",
                                        "justifyContent": "space-between",
                                        "marginBottom": "28px",
                                    },
                                    children=[
                                        html.Div(id="detail-title"),
                                        html.Button(
                                            "← Overview",
                                            id="back-button",
                                            n_clicks=0,
                                            className="back-btn",
                                        ),
                                    ],
                                ),
                                html.Div(id="detail-content"),
                            ],
                        ),
                    ],
                ),
            ],
        )

    app.layout = _serve_layout

    app.clientside_callback(
        """
        function(n_clicks) {
            if (n_clicks && n_clicks > 0) {
                window.location.reload();
            }
            return "";
        }
        """,
        Output("reload-dummy", "children"),
        Input("reload-button", "n_clicks"),
        prevent_initial_call=True,
    )

    # ── Callback: navigate between overview and detail ────────────────────────
    @app.callback(
        [
            Output("detail-content","children"),
            Output("detail-page",   "style"),
            Output("overview-page", "style"),
            Output("detail-title",  "children"),
        ],
        [
            Input("service-table", "active_cell"),
            Input("back-button",   "n_clicks"),
        ],
        [
            State("service-table", "data"),
        ],
        prevent_initial_call=True,
    )
    def navigate(active_cell, back_clicks, table_data):
        ctx = dash.callback_context
        if not ctx.triggered:
            raise dash.exceptions.PreventUpdate

        trigger = ctx.triggered[0]["prop_id"].split(".")[0]

        # Back to overview
        if trigger == "back-button":
            return None, {"display": "none"}, {"display": "block"}, None

        # Into detail view
        if trigger == "service-table" and active_cell and table_data:
            row_idx = active_cell["row"]
            if row_idx < len(table_data):
                name    = table_data[row_idx]["service_name"]
                rank_df_local, mapping_df_local, _ = _load_all()
                svc_row = rank_df_local[rank_df_local["service_name"] == name].iloc[0]
                is_susp = bool(svc_row["is_suspicious"])
                accent  = T["red"] if is_susp else T["cyan"]

                title = html.Div([
                    html.P("Service Detail", className="section-label", style={"marginBottom": "6px"}),
                    html.H2(name, style={
                        "fontFamily":    T["font_mono"],
                        "fontSize":      "26px",
                        "fontWeight":    "600",
                        "color":         accent,
                        "letterSpacing": "-0.5px",
                    }),
                ])

                content = _detail_layout(name, rank_df_local, mapping_df_local)
                return content, {"display": "block"}, {"display": "none"}, title

        raise dash.exceptions.PreventUpdate

    return app


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main(data_dir: Optional[Path] = None) -> int:
    app = create_app(data_dir=data_dir)

    host = os.environ.get("BOUNDARY_ANALYZER_DASH_HOST", "127.0.0.1")
    port = int(os.environ.get("BOUNDARY_ANALYZER_DASH_PORT", "8050"))

    print(f"\n  ◈ Boundary Analyzer — http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())