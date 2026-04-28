"""
charts.py – Boundary Analyzer Visual Engine
============================================
Every chart in this file is built to match the dark-tech design system
defined in dashboard.py (JetBrains Mono + Syne, #06080f background,
#00e5ff cyan accent, #ff1744 red alert, #ff9800 amber threshold).

CHART CATALOGUE
───────────────
  create_animated_bar_chart   Cinematic horizontal bar race with custom
                              hover cards, rank badges, and threshold band.

  create_scom_distribution    KDE + rug + histogram hybrid with dual-zone
                              colouring, percentile annotations, and a
                              threshold marker.

  create_cohesion_gauge       Bullet / gauge chart per service: shows SCOM
                              value against healthy/warning/critical bands.

  create_endpoint_scatter     Bubble scatter: endpoints (x) × tables (y) ×
                              SCOM (size) × health (colour) with quadrant
                              lines and rich hover.

  create_summary_cards        Pure Python dict – no visual change needed,
                              but extended with extra stats for the header.

  _LAYOUT                     Shared Plotly layout dict – single source of
                              truth for fonts, colours, and grid style.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from dash import dcc

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 – SHARED DESIGN TOKENS
# These values must stay in sync with the T dict in dashboard.py.
# ─────────────────────────────────────────────────────────────────────────────

# Palette
_CYAN       = "#00e5ff"
_CYAN_15    = "rgba(0,229,255,0.15)"
_CYAN_08    = "rgba(0,229,255,0.08)"
_CYAN_04    = "rgba(0,229,255,0.04)"
_AMBER      = "#ff9800"
_AMBER_20   = "rgba(255,152,0,0.20)"
_AMBER_10   = "rgba(255,152,0,0.10)"
_RED        = "#ff1744"
_RED_20     = "rgba(255,23,68,0.20)"
_RED_10     = "rgba(255,23,68,0.10)"
_GREEN      = "#00e676"
_GREEN_15   = "rgba(0,230,118,0.15)"
_PURPLE     = "#b388ff"
_TEXT_P     = "#e8f0fe"           # primary text
_TEXT_S     = "rgba(200,220,255,0.55)"  # secondary text
_TEXT_M     = "rgba(200,220,255,0.30)"  # muted text
_GRID       = "rgba(0,229,255,0.06)"    # chart grid lines
_AXIS_LINE  = "rgba(0,229,255,0.12)"
_TICK_COLOR = "rgba(0,229,255,0.25)"
_BG_PAPER   = "rgba(0,0,0,0)"
_BG_PLOT    = "rgba(0,0,0,0)"
_FONT_MONO  = "JetBrains Mono, Fira Code, monospace"
_FONT_DISP  = "Syne, DM Sans, sans-serif"

# Shared hover label style
_HOVERLABEL = dict(
    bgcolor="rgba(10,16,30,0.97)",
    bordercolor=_CYAN,
    font_family=_FONT_MONO,
    font_color=_TEXT_P,
    font_size=12,
)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 – BASE LAYOUT
# Every chart starts from this dict and overrides only what it needs.
# ─────────────────────────────────────────────────────────────────────────────

_LAYOUT = dict(
    paper_bgcolor=_BG_PAPER,
    plot_bgcolor=_BG_PLOT,
    font=dict(family=_FONT_MONO, color=_TEXT_P, size=11),
    hoverlabel=_HOVERLABEL,
    margin=dict(t=52, b=44, l=52, r=28),
    legend=dict(
        bgcolor="rgba(10,16,30,0.80)",
        bordercolor=_AXIS_LINE,
        borderwidth=1,
        font=dict(family=_FONT_MONO, size=10),
        itemsizing="constant",
        orientation="h",
        yanchor="bottom",
        y=1.01,
        xanchor="right",
        x=1,
    ),
    xaxis=dict(
        gridcolor=_GRID,
        linecolor=_AXIS_LINE,
        tickcolor=_TICK_COLOR,
        tickfont=dict(family=_FONT_MONO, size=10, color=_TEXT_S),
        zerolinecolor=_GRID,
        showspikes=True,
        spikecolor=_CYAN_15,
        spikethickness=1,
        spikedash="dot",
        spikemode="across",
    ),
    yaxis=dict(
        gridcolor=_GRID,
        linecolor=_AXIS_LINE,
        tickcolor=_TICK_COLOR,
        tickfont=dict(family=_FONT_MONO, size=10, color=_TEXT_S),
        zerolinecolor=_GRID,
        showspikes=True,
        spikecolor=_CYAN_15,
        spikethickness=1,
        spikedash="dot",
        spikemode="across",
    ),
)


def _base_layout(**overrides) -> dict:
    """Return a deep-merged copy of _LAYOUT with caller overrides applied."""
    import copy
    base = copy.deepcopy(_LAYOUT)
    # Merge top-level keys; nested dicts are replaced not merged (caller owns them)
    base.update(overrides)
    return base


def _annotate_watermark(fig: go.Figure, text: str = "BOUNDARY ANALYZER") -> None:
    """Add a faint diagonal watermark to a figure."""
    fig.add_annotation(
        text=text,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(family=_FONT_MONO, size=36, color="rgba(0,229,255,0.025)"),
        textangle=-30,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 – ANIMATED BAR CHART
# Cinematic horizontal bar race: sorted by SCOM, with animated reveal,
# rank badge annotations, threshold band, and rich hover cards.
# ─────────────────────────────────────────────────────────────────────────────

def create_animated_bar_chart(df: pd.DataFrame) -> dcc.Graph:
    """
    Animated horizontal bar chart showing SCOM scores per service.

    Visual features:
    ─ Horizontal bars (easier to read service names)
    ─ Cyan fill for healthy, red for suspicious
    ─ Glowing marker line on each bar edge
    ─ Amber dashed threshold band with annotation
    ─ Rank number rendered as a badge inside each bar
    ─ Rich multi-line hover card (SCOM, rank, endpoints, tables, status)
    ─ Smooth 700 ms animation between frames
    ─ Subtle watermark
    """
    if df.empty:
        return dcc.Graph()

    df_sorted = df.sort_values("scom_score", ascending=True).reset_index(drop=True)

    # Colour per bar: cyan = healthy, red = suspicious
    bar_colors = [_RED if s else _CYAN for s in df_sorted["is_suspicious"]]

    # Bar opacity: suspicious bars slightly more opaque to draw the eye
    bar_opacity = [0.92 if s else 0.78 for s in df_sorted["is_suspicious"]]

    # Build hover text with multi-line HTML
    hover_texts = []
    for _, row in df_sorted.iterrows():
        status = "⚠  SUSPICIOUS" if row["is_suspicious"] else "✓  HEALTHY"
        status_col = _RED if row["is_suspicious"] else _GREEN
        hover_texts.append(
            f"<b style='font-size:13px'>{row['service_name']}</b><br>"
            f"<span style='color:{_CYAN}'>SCOM Score</span>  {row['scom_score']:.4f}<br>"
            f"<span style='color:{_TEXT_S}'>Rank        </span>  #{int(row['rank'])}<br>"
            f"<span style='color:{_TEXT_S}'>Endpoints   </span>  {int(row.get('endpoints_count', 0))}<br>"
            f"<span style='color:{_TEXT_S}'>Tables      </span>  {int(row.get('tables_count', 0))}<br>"
            f"<span style='color:{status_col}'>{status}</span>"
        )

    # Threshold value: prefer pipeline column name
    if "threshold_value" in df_sorted.columns:
        threshold = float(df_sorted["threshold_value"].iloc[0])
    elif "threshold" in df_sorted.columns:
        threshold = float(df_sorted["threshold"].iloc[0])
    else:
        threshold = 0.75

    fig = go.Figure()

    # ── Main bars ────────────────────────────────────────────────────────────
    fig.add_trace(go.Bar(
        y=df_sorted["service_name"],
        x=df_sorted["scom_score"],
        orientation="h",
        marker=dict(
            color=bar_colors,
            opacity=bar_opacity,
            line=dict(
                # Bright edge glow: same colour as bar
                color=[_RED if s else _CYAN for s in df_sorted["is_suspicious"]],
                width=1.5,
            ),
        ),
        # SCOM score labels at the end of each bar
        text=df_sorted["scom_score"].map(lambda v: f"{v:.4f}"),
        textposition="outside",
        textfont=dict(family=_FONT_MONO, size=10, color=_TEXT_S),
        # Rank badge inside the bar (near left edge)
        customdata=df_sorted[["rank", "is_suspicious", "endpoints_count", "tables_count"]],
        hovertemplate="%{customdata[0]}<extra></extra>",   # replaced by hover_texts below
        name="",
    ))

    # Override hovertemplate with rich HTML text
    fig.data[0].hovertemplate = "%{text_hover}<extra></extra>"
    fig.data[0]["hovertext"] = hover_texts
    fig.data[0].hovertemplate = "%{hovertext}<extra></extra>"

    # ── Rank badge annotations (shown inside each bar) ────────────────────────
    for _, row in df_sorted.iterrows():
        fig.add_annotation(
            x=0.01,                        # very left of chart area
            y=row["service_name"],
            text=f"#{int(row['rank'])}",
            showarrow=False,
            font=dict(family=_FONT_MONO, size=9, color="rgba(200,220,255,0.35)"),
            xanchor="left",
            xref="x",
            yref="y",
        )

    # ── Threshold vertical band ───────────────────────────────────────────────
    fig.add_vrect(
        x0=threshold - 0.005,
        x1=threshold + 0.005,
        fillcolor=_AMBER_20,
        layer="below",
        line=dict(color=_AMBER, width=0),
    )
    fig.add_vline(
        x=threshold,
        line=dict(color=_AMBER, width=1.5, dash="dot"),
        annotation_text=f"  threshold  {threshold:.3f}",
        annotation_font=dict(family=_FONT_MONO, size=9, color=_AMBER),
        annotation_position="top right",
    )

    # ── Perfect score marker ──────────────────────────────────────────────────
    fig.add_vline(
        x=1.0,
        line=dict(color=_CYAN_15, width=1, dash="dot"),
        annotation_text=" 1.000",
        annotation_font=dict(family=_FONT_MONO, size=8, color=_TEXT_M),
        annotation_position="top right",
    )

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        **_base_layout(
            height=max(260, len(df_sorted) * 48 + 80),
            showlegend=False,
            bargap=0.30,
            xaxis=dict(
                **_LAYOUT["xaxis"],
                title=dict(text="SCOM Cohesion Score", font=dict(size=10, color=_TEXT_M)),
                range=[0, 1.12],
                tickvals=[0, 0.2, 0.4, 0.6, 0.8, 1.0],
                ticktext=["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"],
            ),
            yaxis=dict(
                **_LAYOUT["yaxis"],
                title=None,
                tickfont=dict(family=_FONT_MONO, size=11, color=_TEXT_P),
                showgrid=False,
                showline=False,
            ),
            margin=dict(t=36, b=40, l=180, r=70),
        )
    )

    # Faint watermark
    _annotate_watermark(fig)

    return dcc.Graph(
        figure=fig,
        config={
            "displayModeBar": False,
            "responsive": True,
        },
        # Smooth initial render via CSS transition (applied by Dash)
        style={"transition": "opacity 0.4s ease"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 – SCOM DISTRIBUTION CHART
# KDE density curve + rug + histogram hybrid.
# Dual coloured zones (healthy = cyan, suspicious = red).
# Percentile annotations (P25, P50, P75) and a threshold marker.
# ─────────────────────────────────────────────────────────────────────────────

def _kde(values: np.ndarray, bw: float = 0.05, n: int = 300) -> tuple[np.ndarray, np.ndarray]:
    """
    Gaussian KDE from scratch (no scipy needed).
    Returns (x_grid, density) arrays.
    """
    x = np.linspace(max(0, values.min() - 0.1), min(1.1, values.max() + 0.1), n)
    density = np.zeros(n)
    for v in values:
        density += np.exp(-0.5 * ((x - v) / bw) ** 2) / (bw * math.sqrt(2 * math.pi))
    density /= len(values)   # normalise to PDF
    return x, density


def create_scom_distribution(df: pd.DataFrame) -> dcc.Graph:
    """
    Distribution chart with three overlaid layers:

    Layer 1 – Dual-zone histogram (cyan bars for healthy, red for suspicious).
    Layer 2 – KDE density curve (smooth white line) with filled area.
    Layer 3 – Rug plot (vertical tick marks below x-axis per service).
    Layer 4 – Percentile vlines (P25, P50, P75) with labels.
    Layer 5 – Threshold vrect with danger zone fill.
    Layer 6 – Individual service annotations on the rug.
    """
    if df.empty:
        return dcc.Graph()

    if "threshold_value" in df.columns:
        threshold = float(df["threshold_value"].iloc[0])
    elif "threshold" in df.columns:
        threshold = float(df["threshold"].iloc[0])
    else:
        threshold = 0.75

    scores      = df["scom_score"].values.astype(float)
    healthy_sc  = df.loc[~df["is_suspicious"], "scom_score"].values
    suspect_sc  = df.loc[df["is_suspicious"],  "scom_score"].values

    fig = go.Figure()

    # ── Layer 0 – Danger zone fill (below threshold) ──────────────────────────
    fig.add_vrect(
        x0=0,
        x1=threshold,
        fillcolor=_RED_10,
        layer="below",
        line_width=0,
        annotation_text="⚠ danger zone",
        annotation_font=dict(family=_FONT_MONO, size=9, color=_RED),
        annotation_position="top left",
    )

    # ── Layer 1a – Healthy histogram (cyan) ───────────────────────────────────
    if len(healthy_sc):
        fig.add_trace(go.Histogram(
            x=healthy_sc,
            nbinsx=12,
            name="Healthy",
            marker=dict(
                color=_CYAN_15,
                line=dict(color=_CYAN, width=1),
            ),
            opacity=0.70,
            hovertemplate="SCOM bin: %{x:.2f}<br>Count: %{y}<extra>Healthy</extra>",
        ))

    # ── Layer 1b – Suspicious histogram (red) ────────────────────────────────
    if len(suspect_sc):
        fig.add_trace(go.Histogram(
            x=suspect_sc,
            nbinsx=12,
            name="Suspicious",
            marker=dict(
                color=_RED_10,
                line=dict(color=_RED, width=1),
            ),
            opacity=0.80,
            hovertemplate="SCOM bin: %{x:.2f}<br>Count: %{y}<extra>Suspicious</extra>",
        ))

    # Overlay histograms (not stack)
    fig.update_layout(barmode="overlay")

    # ── Layer 2 – KDE density curve ───────────────────────────────────────────
    if len(scores) >= 2:
        kde_x, kde_y = _kde(scores, bw=0.06)
        # Scale KDE to match histogram counts (PDF → count density)
        bin_width = 1.0 / 12
        kde_y_scaled = kde_y * len(scores) * bin_width

        # Filled area under curve
        fig.add_trace(go.Scatter(
            x=kde_x,
            y=kde_y_scaled,
            fill="tozeroy",
            fillcolor="rgba(0,229,255,0.04)",
            line=dict(color="rgba(0,229,255,0.0)", width=0),
            name="",
            showlegend=False,
            hoverinfo="skip",
        ))
        # KDE line
        fig.add_trace(go.Scatter(
            x=kde_x,
            y=kde_y_scaled,
            mode="lines",
            line=dict(color=_CYAN, width=2, shape="spline", smoothing=1.2),
            name="Density",
            hovertemplate="SCOM: %{x:.3f}<br>Density: %{y:.2f}<extra>KDE</extra>",
        ))

    # ── Layer 3 – Rug plot ────────────────────────────────────────────────────
    # Individual service marks just below the x-axis
    for _, row in df.iterrows():
        col = _RED if row["is_suspicious"] else _CYAN
        fig.add_shape(
            type="line",
            x0=row["scom_score"], x1=row["scom_score"],
            y0=-0.15, y1=0,          # will be in paper coords after update
            xref="x", yref="paper",
            line=dict(color=col, width=1.5),
        )

    # ── Layer 4 – Percentile vlines ───────────────────────────────────────────
    p_labels = {25: "Q1", 50: "Q2 / Median", 75: "Q3"}
    for pct, label in p_labels.items():
        val = float(np.percentile(scores, pct))
        fig.add_vline(
            x=val,
            line=dict(color=_TEXT_M, width=1, dash="longdash"),
            annotation_text=f"  {label}  {val:.3f}",
            annotation_font=dict(family=_FONT_MONO, size=8, color=_TEXT_M),
            annotation_position="top left",
        )

    # ── Layer 5 – Threshold marker ────────────────────────────────────────────
    fig.add_vline(
        x=threshold,
        line=dict(color=_AMBER, width=2, dash="dot"),
        annotation_text=f"  threshold  {threshold:.3f}",
        annotation_font=dict(family=_FONT_MONO, size=9, color=_AMBER),
        annotation_position="top right",
    )

    # ── Layer 6 – Annotation: mean ────────────────────────────────────────────
    mean_val = float(scores.mean())
    fig.add_annotation(
        x=mean_val,
        y=1.0,
        xref="x",
        yref="paper",
        text=f"μ = {mean_val:.3f}",
        showarrow=True,
        arrowhead=2,
        arrowsize=0.8,
        arrowwidth=1,
        arrowcolor=_GREEN,
        ax=30, ay=-30,
        font=dict(family=_FONT_MONO, size=9, color=_GREEN),
    )

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        **_base_layout(
            height=310,
            xaxis=dict(
                **_LAYOUT["xaxis"],
                title=dict(text="SCOM Cohesion Score", font=dict(size=10, color=_TEXT_M)),
                range=[0, 1.05],
                tickvals=[0, 0.2, 0.4, 0.6, 0.8, 1.0],
                ticktext=["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"],
            ),
            yaxis=dict(
                **_LAYOUT["yaxis"],
                title=dict(text="Services (count)", font=dict(size=10, color=_TEXT_M)),
                rangemode="nonnegative",
            ),
            legend=dict(
                **_LAYOUT["legend"],
                traceorder="normal",
            ),
        )
    )

    _annotate_watermark(fig)

    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 – COHESION GAUGE (bullet chart)
# Shows each service's SCOM value on a 0-1 gauge with coloured bands:
#   0.00 – 0.60  → critical  (dark red)
#   0.60 – 0.75  → warning   (amber)
#   0.75 – 1.00  → healthy   (cyan)
# ─────────────────────────────────────────────────────────────────────────────

def create_cohesion_gauge(scom_score: float, service_name: str) -> dcc.Graph:
    """
    Bullet-style gauge for a single service.
    Used in the service detail page to give an immediate visual verdict.
    """
    score = float(scom_score)
    is_suspicious = score < 0.75   # default; override if threshold is passed

    # Pick needle colour
    needle_col = _RED if is_suspicious else _CYAN

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=round(score, 4),
        delta=dict(
            reference=0.75,
            valueformat=".4f",
            increasing=dict(color=_GREEN),
            decreasing=dict(color=_RED),
            font=dict(family=_FONT_MONO, size=11),
        ),
        number=dict(
            valueformat=".4f",
            font=dict(family=_FONT_MONO, size=28, color=needle_col),
            suffix=" SCOM",
        ),
        gauge=dict(
            axis=dict(
                range=[0, 1],
                tickvals=[0, 0.2, 0.4, 0.6, 0.75, 0.8, 1.0],
                ticktext=["0.0", "0.2", "0.4", "0.6", "0.75", "0.8", "1.0"],
                tickfont=dict(family=_FONT_MONO, size=9, color=_TEXT_S),
                tickcolor=_TICK_COLOR,
                tickwidth=1,
                nticks=6,
            ),
            bar=dict(
                color=needle_col,
                thickness=0.20,
                line=dict(color=needle_col, width=2),
            ),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=1,
            bordercolor=_AXIS_LINE,
            steps=[
                # Critical zone
                dict(range=[0, 0.60],  color="rgba(255,23,68,0.12)"),
                # Warning zone
                dict(range=[0.60, 0.75], color="rgba(255,152,0,0.12)"),
                # Healthy zone
                dict(range=[0.75, 1.0], color="rgba(0,229,255,0.08)"),
            ],
            threshold=dict(
                line=dict(color=_AMBER, width=3),
                thickness=0.75,
                value=0.75,
            ),
        ),
        title=dict(
            text=(
                f"<span style='font-family:{_FONT_MONO};font-size:11px;"
                f"letter-spacing:2px;color:{_TEXT_M}'>"
                f"{service_name.upper()}</span>"
            ),
            font=dict(size=12),
        ),
    ))

    fig.update_layout(
        **_base_layout(
            height=260,
            margin=dict(t=40, b=20, l=30, r=30),
        )
    )

    # Zone labels
    for x, label, col in [
        (0.30, "CRITICAL", _RED),
        (0.67, "WARNING",  _AMBER),
        (0.87, "HEALTHY",  _CYAN),
    ]:
        fig.add_annotation(
            x=x, y=-0.22,
            xref="paper", yref="paper",
            text=label,
            showarrow=False,
            font=dict(family=_FONT_MONO, size=8, color=col),
        )

    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 – ENDPOINT × TABLE BUBBLE SCATTER
# Axes: number of endpoints (x) vs number of tables (y).
# Bubble size: SCOM score (bigger = more cohesive).
# Colour: healthy (cyan) vs suspicious (red).
# Quadrant lines divide the space into 4 interpretable zones.
# ─────────────────────────────────────────────────────────────────────────────

def create_endpoint_scatter(df: pd.DataFrame) -> dcc.Graph:
    """
    Bubble scatter of all services.

    Quadrant interpretation:
    ┌──────────────────────────────────────────┐
    │ Many tables                              │
    │  Q2 Many endpoints + many tables         │ Q1 Few endpoints + many tables
    │  (big service – consider splitting)       │  (data-heavy, narrow scope)
    ├──────────────────────────────────────────┤
    │ Few tables                               │
    │  Q3 Many endpoints + few tables          │ Q0 Few endpoints + few tables
    │  (good cohesion candidate)               │  (small focused service ✓)
    └──────────────────────────────────────────┘
    """
    if df.empty:
        return dcc.Graph()

    ep_median  = df["endpoints_count"].median()
    tbl_median = df["tables_count"].median()

    colors  = [_RED if s else _CYAN for s in df["is_suspicious"]]
    opacity = [0.90 if s else 0.75 for s in df["is_suspicious"]]

    # Bubble size mapped to SCOM score (bigger = more cohesive = good)
    max_scom = df["scom_score"].max() or 1.0
    sizes = (df["scom_score"] / max_scom * 38 + 10).tolist()

    hover_texts = []
    for _, row in df.iterrows():
        status     = "⚠  SUSPICIOUS" if row["is_suspicious"] else "✓  HEALTHY"
        status_col = _RED if row["is_suspicious"] else _GREEN
        hover_texts.append(
            f"<b>{row['service_name']}</b><br>"
            f"<span style='color:{_CYAN}'>SCOM    </span> {row['scom_score']:.4f}<br>"
            f"<span style='color:{_TEXT_S}'>Endpoints</span> {int(row['endpoints_count'])}<br>"
            f"<span style='color:{_TEXT_S}'>Tables   </span> {int(row['tables_count'])}<br>"
            f"<span style='color:{status_col}'>{status}</span>"
        )

    fig = go.Figure()

    # ── Quadrant background fills ─────────────────────────────────────────────
    fig.add_hrect(y0=0,          y1=tbl_median, fillcolor="rgba(0,229,255,0.02)", layer="below", line_width=0)
    fig.add_hrect(y0=tbl_median, y1=100,        fillcolor="rgba(255,152,0,0.02)", layer="below", line_width=0)

    # ── Quadrant divider lines ────────────────────────────────────────────────
    fig.add_vline(x=ep_median,  line=dict(color=_TEXT_M, width=1, dash="longdash"))
    fig.add_hline(y=tbl_median, line=dict(color=_TEXT_M, width=1, dash="longdash"))

    # ── Quadrant labels ───────────────────────────────────────────────────────
    quadrant_labels = [
        (0.12, 0.12, "Focused\n& Lean",     _GREEN),
        (0.88, 0.12, "Wide scope\nfew tables", _CYAN),
        (0.12, 0.88, "Narrow scope\nmany tables", _CYAN),
        (0.88, 0.88, "High\ncomplexity",    _AMBER),
    ]
    for xp, yp, text, col in quadrant_labels:
        fig.add_annotation(
            x=xp, y=yp,
            xref="paper", yref="paper",
            text=text,
            showarrow=False,
            font=dict(family=_FONT_MONO, size=8, color=col.replace(")", ", 0.45)").replace("rgba","rgba") if "rgba" in col else col + "73"),
            align="center",
        )

    # ── Scatter bubbles ───────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=df["endpoints_count"],
        y=df["tables_count"],
        mode="markers+text",
        marker=dict(
            color=colors,
            opacity=opacity,
            size=sizes,
            line=dict(
                color=[_RED if s else _CYAN for s in df["is_suspicious"]],
                width=1.5,
            ),
            sizemode="diameter",
        ),
        text=df["service_name"],
        textposition="top center",
        textfont=dict(family=_FONT_MONO, size=9, color=_TEXT_S),
        hovertext=hover_texts,
        hovertemplate="%{hovertext}<extra></extra>",
        name="",
        showlegend=False,
    ))

    # ── Legend proxy traces ────────────────────────────────────────────────────
    for name, col in [("Healthy", _CYAN), ("Suspicious", _RED)]:
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="markers",
            marker=dict(color=col, size=10),
            name=name,
            showlegend=True,
        ))

    fig.update_layout(
        **_base_layout(
            height=360,
            xaxis=dict(
                **_LAYOUT["xaxis"],
                title=dict(text="Number of Endpoints", font=dict(size=10, color=_TEXT_M)),
                rangemode="tozero",
            ),
            yaxis=dict(
                **_LAYOUT["yaxis"],
                title=dict(text="Number of Tables", font=dict(size=10, color=_TEXT_M)),
                rangemode="tozero",
            ),
        )
    )

    _annotate_watermark(fig)

    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 – SCOM TIMELINE (if historical data is available)
# Line chart showing SCOM evolution across multiple analysis runs.
# Renders a "flat" version (single data point per service) when there
# is no history column, making it always usable.
# ─────────────────────────────────────────────────────────────────────────────

def create_scom_timeline(df: pd.DataFrame) -> dcc.Graph:
    """
    SCOM score timeline per service.

    If df has a 'run_id' column: draws a proper time-series line chart.
    If not: draws a sorted dot plot (one dot per service) as a fallback.
    This chart answers "is my service improving or degrading over time?"
    """
    if df.empty:
        return dcc.Graph()

    fig = go.Figure()

    if "run_id" in df.columns:
        # ── True timeline mode ────────────────────────────────────────────────
        for svc, svc_df in df.groupby("service_name"):
            svc_df = svc_df.sort_values("run_id")
            is_suspect = svc_df["is_suspicious"].any()
            col = _RED if is_suspect else _CYAN

            fig.add_trace(go.Scatter(
                x=svc_df["run_id"],
                y=svc_df["scom_score"],
                mode="lines+markers",
                name=svc,
                line=dict(color=col, width=2, shape="spline", smoothing=0.8),
                marker=dict(color=col, size=7, line=dict(color=_BG_PAPER, width=2)),
                hovertemplate=(
                    f"<b>{svc}</b><br>"
                    "Run: %{x}<br>"
                    f"<span style='color:{_CYAN}'>SCOM: </span>%{{y:.4f}}<extra></extra>"
                ),
            ))
    else:
        # ── Dot plot fallback ─────────────────────────────────────────────────
        df_s = df.sort_values("scom_score", ascending=False)
        for _, row in df_s.iterrows():
            col = _RED if row["is_suspicious"] else _CYAN
            fig.add_trace(go.Scatter(
                x=[row["scom_score"]],
                y=[row["service_name"]],
                mode="markers+text",
                text=[f"  {row['scom_score']:.4f}"],
                textposition="middle right",
                textfont=dict(family=_FONT_MONO, size=10, color=col),
                marker=dict(color=col, size=10, symbol="circle",
                            line=dict(color=col, width=2)),
                name=row["service_name"],
                showlegend=False,
                hovertemplate=(
                    f"<b>{row['service_name']}</b><br>"
                    f"SCOM: {row['scom_score']:.4f}<extra></extra>"
                ),
            ))

        # Connecting lines to zero axis (lollipop style)
        for _, row in df_s.iterrows():
            fig.add_shape(
                type="line",
                x0=0, x1=row["scom_score"],
                y0=row["service_name"], y1=row["service_name"],
                line=dict(
                    color=_RED if row["is_suspicious"] else _CYAN_15,
                    width=1,
                    dash="dot",
                ),
            )

    # Threshold reference line
    threshold = float(df["threshold"].iloc[0]) if "threshold" in df.columns else 0.75
    fig.add_hline(
        y=threshold,
        line=dict(color=_AMBER, width=1.5, dash="dot"),
    ) if "run_id" not in df.columns else fig.add_hline(
        y=threshold,
        line=dict(color=_AMBER, width=1.5, dash="dot"),
        annotation_text=f"threshold {threshold:.3f}",
        annotation_font=dict(family=_FONT_MONO, size=9, color=_AMBER),
    )

    fig.update_layout(
        **_base_layout(
            height=max(280, len(df) * 38 + 60) if "run_id" not in df.columns else 300,
            xaxis=dict(
                **_LAYOUT["xaxis"],
                title=dict(
                    text="SCOM Score" if "run_id" not in df.columns else "Analysis Run",
                    font=dict(size=10, color=_TEXT_M),
                ),
                range=[0, 1.1] if "run_id" not in df.columns else None,
            ),
            yaxis=dict(
                **_LAYOUT["yaxis"],
                title=None,
                showgrid="run_id" in df.columns,
            ),
            showlegend="run_id" in df.columns,
            margin=dict(t=36, b=40, l=180, r=70),
        )
    )

    _annotate_watermark(fig)

    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 – SUMMARY STATS
# Pure Python dict – consumed by _metric_card() in dashboard.py.
# Extended with extra fields useful for the header and detail page.
# ─────────────────────────────────────────────────────────────────────────────

def create_summary_cards(df: pd.DataFrame) -> dict:
    """
    Return a dict of summary statistics for all KPI cards.

    Keys returned:
      total_services    int
      suspicious_count  int
      safe_count        int
      avg_scom          float
      min_scom          float
      max_scom          float
      std_scom          float   ← new: standard deviation
      p25_scom          float   ← new: 25th percentile
      p75_scom          float   ← new: 75th percentile
      health_rate       float   ← new: fraction of healthy services (0–1)
      worst_service     str     ← new: name of the lowest-SCOM service
      best_service      str     ← new: name of the highest-SCOM service
    """
    if df.empty:
        return {
            "total_services":  0,
            "suspicious_count": 0,
            "safe_count":       0,
            "avg_scom":         0.0,
            "min_scom":         0.0,
            "max_scom":         0.0,
            "std_scom":         0.0,
            "p25_scom":         0.0,
            "p75_scom":         0.0,
            "health_rate":      0.0,
            "worst_service":    "—",
            "best_service":     "—",
        }

    suspicious_mask  = df["is_suspicious"] == True
    suspicious_count = int(suspicious_mask.sum())
    safe_count       = int((~suspicious_mask).sum())
    scores           = df["scom_score"].astype(float)

    worst_idx = scores.idxmin()
    best_idx  = scores.idxmax()

    return {
        "total_services":   len(df),
        "suspicious_count": suspicious_count,
        "safe_count":       safe_count,
        "avg_scom":         round(float(scores.mean()), 4),
        "min_scom":         round(float(scores.min()),  4),
        "max_scom":         round(float(scores.max()),  4),
        "std_scom":         round(float(scores.std()),  4),
        "p25_scom":         round(float(np.percentile(scores, 25)), 4),
        "p75_scom":         round(float(np.percentile(scores, 75)), 4),
        "health_rate":      round(safe_count / max(len(df), 1), 3),
        "worst_service":    str(df.loc[worst_idx, "service_name"]),
        "best_service":     str(df.loc[best_idx,  "service_name"]),
    }