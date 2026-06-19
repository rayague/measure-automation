from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dash import dash_table, dcc, html

from boundary_analyzer.dashboard.app import (
    _build_bar_chart,
    _build_distribution,
    _build_heatmap,
    _build_radar_chart,
    _get_data_freshness,
    _load_all,
    _load_llm_analysis,
)
from boundary_analyzer.dashboard.design_tokens import T

logger = logging.getLogger(__name__)


def _metric_card(label: str, value, variant: str = "cyan") -> html.Div:
    val_color = {
        "cyan": T["cyan"],
        "amber": T["amber"],
        "green": T["green"],
        "red": T["red"],
    }.get(variant, T["cyan"])

    return html.Div(
        className=f"metric-card metric-card--{variant}",
        children=[
            html.P(
                label,
                style={
                    "fontFamily": T["font_mono"],
                    "fontSize": "9px",
                    "letterSpacing": "3px",
                    "textTransform": "uppercase",
                    "color": T["text_muted"],
                    "marginBottom": "14px",
                },
            ),
            html.P(
                str(value),
                style={
                    "fontFamily": T["font_mono"],
                    "fontSize": "34px",
                    "fontWeight": "600",
                    "color": val_color,
                    "lineHeight": "1",
                    "letterSpacing": "-1px",
                },
            ),
        ],
    )


def _card(title: str, children, style_extra=None) -> html.Div:
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
        return html.Span(
            [
                html.Span(className="pulse-dot"),
                "SUSPICIOUS",
            ],
            style={
                "fontFamily": T["font_mono"],
                "fontSize": "10px",
                "letterSpacing": "2px",
                "color": T["red"],
                "background": T["red_dim"],
                "border": f"1px solid {T['red']}44",
                "padding": "5px 12px",
                "borderRadius": "4px",
            },
        )
    return html.Span(
        "● HEALTHY",
        style={
            "fontFamily": T["font_mono"],
            "fontSize": "10px",
            "letterSpacing": "2px",
            "color": T["green"],
            "background": T["green_dim"],
            "border": f"1px solid {T['green']}44",
            "padding": "5px 12px",
            "borderRadius": "4px",
        },
    )


def _build_table(df: pd.DataFrame) -> Any:
    if df.empty:
        return dash_table.DataTable()  # type: ignore[attr-defined]

    from boundary_analyzer._utils import classify_scom

    required_cols = ["rank", "service_name", "scom_score", "is_suspicious"]
    available_ = [c for c in required_cols if c in df.columns]
    for extra in ["endpoints_count", "tables_count"]:
        if extra in df.columns:
            available_.append(extra)
    disp = df[available_].copy()
    disp["is_suspicious"] = disp["is_suspicious"].map({True: "⚠ suspect", False: "✓ healthy"})
    disp["cohesion"] = disp["scom_score"].apply(classify_scom)

    return dash_table.DataTable(  # type: ignore[attr-defined]
        id="service-table",
        data=disp.to_dict("records"),
        columns=[
            {"name": "#", "id": "rank"},
            {"name": "Service", "id": "service_name"},
            {"name": "SCOM", "id": "scom_score", "type": "numeric", "format": {"specifier": ".4f"}},
            {"name": "Cohésion", "id": "cohesion"},
            {"name": "Endpoints", "id": "endpoints_count", "type": "numeric"},
            {"name": "Tables", "id": "tables_count", "type": "numeric"},
            {"name": "Status", "id": "is_suspicious"},
        ],
        style_header={
            "backgroundColor": "transparent",
            "borderBottom": f"1px solid {T['border_hot']}",
        },
        style_cell={
            "backgroundColor": "transparent",
            "color": T["text_primary"],
            "border": "none",
        },
        style_data_conditional=[
            {
                "if": {"filter_query": '{is_suspicious} contains "suspect"'},
                "color": T["red"],
                "fontWeight": "600",
            },
            {
                "if": {"filter_query": '{is_suspicious} contains "healthy"'},
                "color": T["green"],
            },
            {
                "if": {"column_id": "rank"},
                "color": T["text_muted"],
                "textAlign": "center",
            },
            {
                "if": {"column_id": "scom_score"},
                "color": T["cyan"],
                "textAlign": "right",
            },
            {
                "if": {"state": "selected"},
                "backgroundColor": T["cyan_dim"],
                "border": f"1px solid {T['cyan_glow']}",
            },
        ],
        page_size=8,
        row_selectable="single",
        style_as_list_view=True,
    )


def _trend_chart_card(base_dir: Path, trend_df: pd.DataFrame | None = None) -> html.Div:
    from boundary_analyzer.dashboard.app import _build_trend_chart, _load_trends

    if trend_df is None:
        trend_df = _load_trends(base_dir)
    if trend_df.empty or len(trend_df.columns) < 2:
        return html.Div()

    fig = _build_trend_chart(trend_df)
    return _card("SCOM Trend — across recent runs", [
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
    ])


def _data_provenance_card(data_dir: Path) -> html.Div:
    rows = []

    rows.append(
        html.Div(
            [
                html.Span("Data source: ", style={"color": T["cyan"], "fontFamily": T["font_mono"], "fontSize": "11px"}),
                html.Span(
                    str(data_dir.resolve()),
                    style={"color": T["text_secondary"], "fontFamily": T["font_mono"], "fontSize": "11px"},
                ),
            ],
            style={"marginBottom": "6px"},
        )
    )

    spans_path = data_dir / "interim" / "spans.csv"
    if spans_path.exists():
        try:
            spans_df = pd.read_csv(spans_path)
            n_spans = len(spans_df)
            n_traces = spans_df["trace_id"].nunique() if "trace_id" in spans_df.columns else "?"
            rows.append(
                html.Div(
                    [
                        html.Span(
                            "Traces / Spans: ",
                            style={"color": T["cyan"], "fontFamily": T["font_mono"], "fontSize": "11px"},
                        ),
                        html.Span(
                            f"{n_traces} traces, {n_spans} spans",
                            style={"color": T["text_secondary"], "fontFamily": T["font_mono"], "fontSize": "11px"},
                        ),
                    ],
                    style={"marginBottom": "6px"},
                )
            )
        except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError, KeyError) as e:
            logger.warning("[Dashboard] Could not read spans data: %s", e)

    rank_path = data_dir / "processed" / "service_rank.csv"
    if rank_path.exists():
        try:
            rank_df = pd.read_csv(rank_path)
            n_svc = len(rank_df)
            n_susp = int(rank_df["is_suspicious"].sum()) if "is_suspicious" in rank_df.columns else "?"
            scom_method = ""
            if "method" in rank_df.columns:
                methods = sorted({str(m) for m in rank_df["method"].dropna().unique()})
                if methods:
                    scom_method = f" — {', '.join(methods)}"
            threshold = ""
            if "threshold_value" in rank_df.columns:
                threshold = f", threshold={float(rank_df['threshold_value'].iloc[0]):.4f}"
            rows.append(
                html.Div(
                    [
                        html.Span("Services: ", style={"color": T["cyan"], "fontFamily": T["font_mono"], "fontSize": "11px"}),
                        html.Span(
                            f"{n_svc} total ({n_susp} suspicious{scom_method}{threshold})",
                            style={"color": T["text_secondary"], "fontFamily": T["font_mono"], "fontSize": "11px"},
                        ),
                    ],
                    style={"marginBottom": "6px"},
                )
            )
        except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError, KeyError) as e:
            logger.warning("[Dashboard] Could not read service rank data: %s", e)

    try:
        mtime = rank_path.stat().st_mtime if rank_path.exists() else 0
        dt = datetime.fromtimestamp(mtime)
        rows.append(
            html.Div(
                [
                    html.Span("Generated: ", style={"color": T["cyan"], "fontFamily": T["font_mono"], "fontSize": "11px"}),
                    html.Span(
                        dt.strftime("%Y-%m-%d %H:%M:%S"),
                        style={"color": T["text_muted"], "fontFamily": T["font_mono"], "fontSize": "11px"},
                    ),
                ]
            )
        )
    except OSError:
        pass

    return _card("Data Provenance", rows, style_extra={"marginBottom": "20px"})


def _overview_layout(rank_df: pd.DataFrame, summary: dict, base_dir: Path,
                     trend_df: pd.DataFrame | None = None) -> html.Div:
    total = summary.get("total_services", 0)
    suspect = summary.get("suspicious_count", 0)
    healthy = summary.get("safe_count", 0)
    avg_scom = summary.get("avg_scom", 0.0)

    return html.Div(
        className="fade-in",
        children=[
            html.Div(
                style={"display": "flex", "gap": "14px", "marginBottom": "24px", "flexWrap": "wrap"},
                children=[
                    _metric_card("Total Services", total, "cyan"),
                    _metric_card("Suspicious", suspect, "red"),
                    _metric_card("Healthy", healthy, "green"),
                    _metric_card("Avg SCOM", f"{avg_scom:.3f}", "amber"),
                ],
            ),
            _card(
                "SCOM Score Distribution — healthy vs suspicious",
                [
                    dcc.Graph(
                        figure=_build_distribution(rank_df),
                        config={"displayModeBar": False},
                    ),
                ],
            ),
            _trend_chart_card(base_dir, trend_df),
            _card(
                "Service Cohesion Ranking",
                [
                    dcc.Graph(
                        figure=_build_bar_chart(rank_df),
                        config={"displayModeBar": False},
                    ),
                ],
            ),
            _card(
                "All Services — click a row to inspect",
                [
                    _build_table(rank_df),
                    html.P(
                        "Click any row to open the service detail view.",
                        style={
                            "fontFamily": T["font_mono"],
                            "fontSize": "10px",
                            "color": T["text_muted"],
                            "marginTop": "12px",
                            "letterSpacing": "1px",
                        },
                    ),
                ],
            ),
            _data_provenance_card(base_dir),
            _card(
                "Definitions — how to read these charts",
                [
                    _definitions_block(rank_df),
                ],
                style_extra={"marginBottom": "0"},
            ),
            _llm_analysis_card(base_dir),
        ],
    )


def _render_inline(text: str) -> list:
    parts: list = []
    pattern = re.compile(r"(\*\*(.+?)\*\*|`([^`]+?)`)")
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            parts.append(text[last_end : m.start()])
        if m.group(2):
            parts.append(html.Strong(m.group(2)))
        elif m.group(3):
            parts.append(
                html.Code(
                    m.group(3),
                    style={
                        "background": "rgba(0, 229, 255, 0.08)",
                        "padding": "1px 5px",
                        "borderRadius": "3px",
                        "fontFamily": T["font_mono"],
                        "fontSize": "11px",
                        "color": T["amber"],
                    },
                )
            )
        last_end = m.end()
    if last_end < len(text):
        parts.append(text[last_end:])
    if not parts:
        parts.append(text)
    return parts


def _llm_analysis_card(base_dir: Path | None = None) -> html.Div:
    analysis = _load_llm_analysis(base_dir)
    if analysis is None:
        return html.Div()

    is_local = analysis.strip().startswith("> **Analysis mode:** Local computed")
    badge = html.Span(
        "LOCAL" if is_local else "LIVE AI",
        style={
            "fontFamily": T["font_mono"],
            "fontSize": "9px",
            "letterSpacing": "2px",
            "padding": "3px 10px",
            "borderRadius": "4px",
            "marginLeft": "12px",
            "verticalAlign": "middle",
            "color": T["amber"] if is_local else T["cyan"],
            "background": T["amber_dim"] if is_local else T["cyan_dim"],
            "border": f"1px solid {T['amber_glow']}" if is_local else f"1px solid {T['cyan_glow']}",
        },
    )

    lines = analysis.split("\n")
    children: list[Any] = [badge]
    in_code_block = False
    code_lines: list[str] = []
    in_list = False
    list_items: list[Any] = []
    in_table = False
    table_rows: list[list[str]] = []

    th_style = {
        "fontFamily": T["font_mono"],
        "fontSize": "11px",
        "color": T["cyan"],
        "padding": "6px 10px",
        "borderBottom": f"1px solid {T['border_hot']}",
        "textAlign": "left",
        "fontWeight": "600",
        "letterSpacing": "1px",
    }
    td_style = {
        "fontFamily": T["font_mono"],
        "fontSize": "11px",
        "color": T["text_primary"],
        "padding": "5px 10px",
        "borderBottom": f"1px solid {T['border']}",
    }
    table_style = {
        "width": "100%",
        "borderCollapse": "collapse",
        "background": "rgba(0, 229, 255, 0.03)",
        "borderRadius": "6px",
        "overflow": "hidden",
        "marginBottom": "10px",
    }

    p_style = {
        "fontFamily": T["font_display"],
        "fontSize": "12px",
        "color": T["text_secondary"],
        "lineHeight": "1.6",
        "marginBottom": "6px",
    }

    def _flush_list():
        nonlocal in_list, list_items
        if list_items:
            children.append(
                html.Ul(
                    list_items,
                    style={
                        "margin": "0 0 8px 0",
                        "paddingLeft": "20px",
                        "listStyle": "none",
                    },
                )
            )
            list_items = []
        in_list = False

    def _flush_table():
        nonlocal in_table, table_rows
        if not table_rows:
            return
        header = table_rows[0]
        body = table_rows[1:]
        cells: list = []
        if header:
            cells.append(html.Thead(html.Tr([html.Th(h, style=th_style) for h in header])))
        if body:
            cells.append(html.Tbody([html.Tr([html.Td(c, style=td_style) for c in row]) for row in body]))
        children.append(html.Table(cells, style=table_style))
        table_rows = []
        in_table = False

    for line in lines:
        raw = line
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                children.append(
                    html.Pre(
                        "\n".join(code_lines),
                        style={
                            "background": "rgba(0, 229, 255, 0.04)",
                            "border": f"1px solid {T['border']}",
                            "borderRadius": "8px",
                            "padding": "12px 16px",
                            "fontFamily": T["font_mono"],
                            "fontSize": "11px",
                            "color": T["cyan"],
                            "overflowX": "auto",
                            "marginBottom": "10px",
                            "lineHeight": "1.5",
                        },
                    )
                )
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(raw)
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            _flush_list()
            parts = [p.strip() for p in stripped.split("|")[1:-1]]
            if not any(c.isalpha() for c in stripped.replace("|", "").replace("-", "").replace(":", "").strip()):
                continue
            if not in_table:
                in_table = True
                table_rows = [parts]
            else:
                table_rows.append(parts)
            continue

        if not stripped:
            _flush_table()
            _flush_list()
            continue

        if stripped == "---":
            _flush_table()
            _flush_list()
            children.append(
                html.Hr(
                    style={
                        "border": "none",
                        "borderTop": f"1px solid {T['border']}",
                        "margin": "16px 0",
                    }
                )
            )
            continue

        if stripped.startswith(">"):
            _flush_table()
            _flush_list()
            quote_text = stripped.lstrip("> ").strip()
            children.append(
                html.Div(
                    _render_inline(quote_text),
                    style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "11px",
                        "color": T["text_muted"],
                        "fontStyle": "italic",
                        "padding": "4px 12px",
                        "borderLeft": f"3px solid {T['border']}",
                        "marginBottom": "8px",
                        "lineHeight": "1.5",
                    },
                )
            )
            continue

        if stripped.startswith("## ") and not stripped.startswith("### ") and not stripped.startswith("#### "):
            _flush_list()
            _flush_table()
            text = stripped[3:].strip()
            children.append(
                html.H4(
                    _render_inline(text),
                    style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "13px",
                        "color": T["cyan"],
                        "marginTop": "14px",
                        "marginBottom": "6px",
                    },
                )
            )
            continue

        if stripped.startswith("### ") or stripped.startswith("#### "):
            _flush_list()
            _flush_table()
            text = stripped.lstrip("# ").strip()
            children.append(
                html.H5(
                    _render_inline(text),
                    style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "12px",
                        "color": T["amber"],
                        "marginTop": "16px",
                        "marginBottom": "6px",
                    },
                )
            )
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            _flush_table()
            in_list = True
            item_text = stripped[2:].strip()
            list_items.append(
                html.Li(
                    [html.Span("▸ ", style={"color": T["cyan"], "fontSize": "10px"})] + _render_inline(item_text),
                    style={
                        "fontSize": "12px",
                        "color": T["text_secondary"],
                        "marginBottom": "2px",
                        "lineHeight": "1.6",
                    },
                )
            )
            continue

        if stripped.startswith("**") and "**" in stripped[2:]:
            _flush_table()
            _flush_list()
            if ":" in stripped[3:]:
                children.append(
                    html.H5(
                        _render_inline(stripped),
                        style={
                            "fontFamily": T["font_mono"],
                            "fontSize": "12px",
                            "color": T["amber"],
                            "marginTop": "12px",
                            "marginBottom": "4px",
                        },
                    )
                )
            else:
                children.append(
                    html.H4(
                        _render_inline(stripped),
                        style={
                            "fontFamily": T["font_mono"],
                            "fontSize": "13px",
                            "color": T["cyan"],
                            "marginTop": "14px",
                            "marginBottom": "6px",
                        },
                    )
                )
            continue

        _flush_table()
        _flush_list()
        children.append(html.P(_render_inline(stripped), style=p_style))

    _flush_table()
    _flush_list()
    return _card("AI-Powered Analysis", children, style_extra={"marginBottom": "0"})


def _definitions_block(rank_df: pd.DataFrame) -> html.Div:
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
                html.Span(
                    term + ": ",
                    style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "12px",
                        "color": T["cyan"],
                    },
                ),
                html.Span(
                    meaning,
                    style={
                        "fontFamily": T["font_display"],
                        "fontSize": "12.5px",
                        "color": T["text_secondary"],
                    },
                ),
            ],
        )

    thresh_txt = f"{threshold_value:.4f}" if threshold_value is not None else "(not available)"
    method_txt = f"method={threshold_method}" if threshold_method else "method is configured in settings.yaml"

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
                "A normalized 0–1 view of multiple indicators. 'Raw' values appear in hover; normalization uses caps (endpoints≤20, tables≤15) and rank scaling.",
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
    raw = row.get("is_suspicious")
    is_suspicious = bool(raw) if pd.notna(raw) else False
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


def _build_data_warning(
    rank_df_local: pd.DataFrame,
    mapping_df_local: pd.DataFrame,
    base_dir: Path,
) -> html.Div | None:
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
                html.Div(
                    "No data found",
                    style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "12px",
                        "color": T["text_primary"],
                        "marginBottom": "6px",
                    },
                ),
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
                html.Div(
                    "ℹ️ HTTP-only mode",
                    style={
                        "fontFamily": T["font_mono"],
                        "fontSize": "12px",
                        "color": T["amber"],
                        "marginBottom": "6px",
                    },
                ),
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


def _build_page_content(
    base_dir: Path,
    run_id: str = "",
) -> html.Div:
    """Build the main page content (overview + detail) for a given data directory.

    Called once on initial load and again whenever the run selector changes.
    """
    from boundary_analyzer.dashboard.app import _load_trends

    rank_df, mapping_df, summary = _load_all(base_dir)
    trend_df = _load_trends(base_dir) if not rank_df.empty else pd.DataFrame()
    data_warning = _build_data_warning(rank_df, mapping_df, base_dir)

    children = [
        data_warning,
        html.Div(
            id="overview-page",
            children=_overview_layout(rank_df, summary, base_dir, trend_df=trend_df) if not rank_df.empty else [],
        ),
        html.Div(
            id="detail-page",
            style={"display": "none"},
            children=[
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
    ]
    return html.Div(children=children)


def _build_run_selector(all_runs: list[dict] | None, current_run_id: str) -> tuple[list[dict], str]:
    """Build dropdown options + selected value for the run selector."""
    opts: list[dict] = [{"label": "Latest data (data/)", "value": ""}]
    selected = ""
    if all_runs:
        for r in all_runs:
            rid = r.get("id", "")
            label = f"{r.get('project_name', '?')} — {rid}"
            opts.append({"label": label, "value": rid})
            if rid == current_run_id:
                selected = rid
    if not selected and current_run_id:
        opts.insert(1, {"label": current_run_id, "value": current_run_id})
        selected = current_run_id
    return opts, selected


def serve_layout(base_dir: Path, run_id: str = "", all_runs: list[dict] | None = None,
                 cli_data_dir_str: str = "") -> html.Div:
    run_options, selected_run = _build_run_selector(all_runs, run_id)
    return html.Div(
        style={"minHeight": "100vh", "background": T["bg_base"], "position": "relative"},
        children=[
            html.Div(id="reload-dummy", style={"display": "none"}),
            dcc.Location(id="url", refresh=False),
            dcc.Store(id="cli-data-dir", data=cli_data_dir_str),
            html.Header(
                style={
                    "position": "sticky",
                    "top": "0",
                    "zIndex": "100",
                    "background": T["bg_header"],
                    "borderBottom": f"1px solid {T['border']}",
                    "backdropFilter": "blur(20px)",
                    "padding": "0 40px",
                    "height": "64px",
                    "display": "flex",
                    "alignItems": "center",
                    "justifyContent": "space-between",
                },
                children=[
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "16px"},
                        children=[
                            html.Div(
                                "◈",
                                style={
                                    "fontSize": "22px",
                                    "color": T["cyan"],
                                    "lineHeight": "1",
                                },
                            ),
                            html.Div(
                                [
                                    html.Span(
                                        "Boundary",
                                        style={
                                            "fontFamily": T["font_display"],
                                            "fontWeight": "800",
                                            "fontSize": "15px",
                                            "color": T["text_primary"],
                                            "letterSpacing": "-0.3px",
                                        },
                                    ),
                                    html.Span(
                                        " Analyzer",
                                        style={
                                            "fontFamily": T["font_display"],
                                            "fontWeight": "400",
                                            "fontSize": "15px",
                                            "color": T["text_secondary"],
                                        },
                                    ),
                                ]
                            ),
                            html.Span(
                                "SCOM v2",
                                style={
                                    "fontFamily": T["font_mono"],
                                    "fontSize": "9px",
                                    "letterSpacing": "2px",
                                    "color": T["cyan"],
                                    "background": T["cyan_dim"],
                                    "border": f"1px solid {T['border_hot']}",
                                    "padding": "3px 8px",
                                    "borderRadius": "4px",
                                },
                            ),
                        ],
                    ),
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "16px"},
                        children=[
                            html.P(
                                "Microservice Cohesion Intelligence",
                                style={
                                    "fontFamily": T["font_mono"],
                                    "fontSize": "10px",
                                    "letterSpacing": "2px",
                                    "color": T["text_muted"],
                                    "textTransform": "uppercase",
                                    "margin": "0",
                                },
                            ),
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
            html.Div(
                style={
                    "background": T["bg_card2"],
                    "borderBottom": f"1px solid {T['border']}",
                    "padding": "8px 40px",
                    "display": "flex",
                    "alignItems": "center",
                    "justifyContent": "space-between",
                    "gap": "12px",
                },
                children=[
                    html.Div(
                        style={"display": "flex", "alignItems": "center", "gap": "16px", "flexWrap": "wrap"},
                        children=[
                            html.Div(
                                style={"display": "flex", "alignItems": "center", "gap": "8px"},
                                children=[
                                    html.Span(
                                        "RUN",
                                        style={
                                            "fontFamily": T["font_mono"],
                                            "fontSize": "8px",
                                            "letterSpacing": "2px",
                                            "color": T["text_muted"],
                                        },
                                    ),
                                    dcc.Dropdown(
                                        id="run-selector",
                                        options=run_options,
                                        value=selected_run,
                                        clearable=False,
                                        style={
                                            "width": "320px",
                                            "fontFamily": T["font_mono"],
                                            "fontSize": "11px",
                                            "background": "transparent",
                                        },
                                    ),
                                ],
                            ),
                            html.Div(
                                id="data-source-info",
                            ),
                        ],
                    ),
                    html.Div(
                        id="data-freshness-info",
                    ),
                ],
            ),
            html.Main(
                id="main-content",
                style={
                    "padding": "32px 40px",
                    "maxWidth": "1400px",
                    "margin": "0 auto",
                    "position": "relative",
                    "zIndex": "1",
                },
                children=[
                    dcc.Store(id="selected-service", data=None),
                    dcc.Loading(
                        id="loading-content",
                        type="circle",
                        children=html.Div(id="page-content"),
                    ),
                ],
            ),
        ],
    )
