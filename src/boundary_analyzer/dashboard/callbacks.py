from __future__ import annotations

import urllib.parse
from pathlib import Path

import dash
from dash import html
from dash.dependencies import Input, Output, State

from boundary_analyzer.auto.run_registry import get_run_path
from boundary_analyzer.dashboard.app import _load_all
from boundary_analyzer.dashboard.design_tokens import T
from boundary_analyzer.dashboard.layout_components import (
    _build_page_content,
    _detail_layout,
)


def _resolve_data_dir(url_search: str = "", cli_fallback: str = "") -> Path:
    """Parse ``?run=<id>`` from URL, or fall back to CLI ``--data-dir`` (then ``data/``)."""
    if url_search:
        parsed = urllib.parse.parse_qs(url_search.lstrip("?"))
        run_ids = parsed.get("run")
        if run_ids and run_ids[0].strip():
            run_id = run_ids[0]
            run_path = get_run_path(run_id)
            if run_path:
                return run_path
    if cli_fallback:
        return Path(cli_fallback)
    from boundary_analyzer.settings_loader import get_data_dir
    return get_data_dir()


def _make_freshness_label(data_dir: Path) -> html.Div:
    """Build the 'UPDATED' label for the data source bar."""
    from boundary_analyzer.dashboard.app import _get_data_freshness

    freshness = _get_data_freshness(data_dir)
    try:
        label = str(data_dir.resolve())
    except OSError:
        label = str(data_dir)
    return html.Div(
        style={"display": "flex", "alignItems": "center", "gap": "16px"},
        children=[
            html.Div(
                style={"display": "flex", "alignItems": "center", "gap": "6px"},
                children=[
                    html.Span("PATH", style={
                        "fontFamily": T["font_mono"], "fontSize": "8px",
                        "letterSpacing": "2px", "color": T["text_muted"],
                    }),
                    html.Span(label, style={
                        "fontFamily": T["font_mono"], "fontSize": "10px",
                        "color": T["cyan"],
                    }),
                ],
            ),
            html.Div(
                style={"display": "flex", "alignItems": "center", "gap": "6px"},
                children=[
                    html.Span("UPDATED", style={
                        "fontFamily": T["font_mono"], "fontSize": "8px",
                        "letterSpacing": "2px", "color": T["text_muted"],
                    }),
                    html.Span(freshness, style={
                        "fontFamily": T["font_mono"], "fontSize": "10px",
                        "color": T["amber"],
                    }),
                ],
            ),
        ],
    )


def register_callbacks(app: dash.Dash) -> None:
    # ── Reload button: full page refresh ──────────────────────────────
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

    # ── Run selector → update URL → triggers main callback ───────────
    app.clientside_callback(
        """
        function(run_id) {
            if (run_id === undefined || run_id === null) return "";
            var params = new URLSearchParams(window.location.search);
            if (run_id) {
                params.set("run", run_id);
            } else {
                params.delete("run");
            }
            var new_search = params.toString();
            if (window.location.search !== "?" + new_search && new_search) {
                window.location.search = "?" + new_search;
            } else if (!new_search && window.location.search) {
                window.location.search = "";
            }
            return "";
        }
        """,
        Output("reload-dummy", "children"),
        Input("run-selector", "value"),
        prevent_initial_call=True,
    )

    # ── Main callback: build page content + update data bar ──────────
    @app.callback(
        [
            Output("page-content", "children"),
            Output("data-source-info", "children"),
            Output("data-freshness-info", "children"),
        ],
        Input("url", "search"),
        State("cli-data-dir", "data"),
        prevent_initial_call=False,
    )
    def load_page(url_search: str | None, cli_fallback: str) -> tuple:
        data_dir = _resolve_data_dir(url_search or "", cli_fallback or "")
        content = _build_page_content(data_dir)
        info = _make_freshness_label(data_dir)
        return content, info, None

    # ── Table navigation between overview ↔ detail ───────────────────
    @app.callback(
        [
            Output("detail-content", "children"),
            Output("detail-page", "style"),
            Output("overview-page", "style"),
            Output("detail-title", "children"),
            Output("selected-service", "data"),
        ],
        [
            Input("service-table", "active_cell"),
            Input("back-button", "n_clicks"),
        ],
        [
            State("service-table", "data"),
            State("url", "search"),
            State("cli-data-dir", "data"),
        ],
        prevent_initial_call=True,
    )
    def navigate(active_cell, back_clicks, table_data, url_search, cli_fallback):
        ctx = dash.callback_context
        if not ctx.triggered:
            raise dash.exceptions.PreventUpdate

        trigger = ctx.triggered[0]["prop_id"].split(".")[0]
        data_dir = _resolve_data_dir(url_search or "", cli_fallback or "")

        if trigger == "back-button":
            return (None, {"display": "none"}, {"display": "block"},
                    None, None)

        if trigger == "service-table" and active_cell and table_data:
            row_idx = active_cell["row"]
            if row_idx < len(table_data):
                name = table_data[row_idx]["service_name"]
                rank_df, mapping_df, _ = _load_all(data_dir)
                if name not in rank_df["service_name"].values:
                    raise dash.exceptions.PreventUpdate
                svc_row = rank_df[rank_df["service_name"] == name].iloc[0]
                is_susp = bool(svc_row["is_suspicious"])
                accent = T["red"] if is_susp else T["cyan"]

                title = html.Div([
                    html.P("Service Detail", className="section-label",
                           style={"marginBottom": "6px"}),
                    html.H2(name, style={
                        "fontFamily": T["font_mono"], "fontSize": "26px",
                        "fontWeight": "600", "color": accent,
                        "letterSpacing": "-0.5px",
                    }),
                ])

                content = _detail_layout(name, rank_df, mapping_df)
                return (content, {"display": "block"}, {"display": "none"},
                        title, name)

        raise dash.exceptions.PreventUpdate
