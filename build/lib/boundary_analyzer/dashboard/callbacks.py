from __future__ import annotations

from pathlib import Path

import dash
from dash import html
from dash.dependencies import Input, Output, State

from boundary_analyzer.dashboard.app import _load_all
from boundary_analyzer.dashboard.design_tokens import T
from boundary_analyzer.dashboard.layout_components import _detail_layout


def register_callbacks(app: dash.Dash, base_dir: Path) -> None:
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

    @app.callback(
        [
            Output("detail-content", "children"),
            Output("detail-page", "style"),
            Output("overview-page", "style"),
            Output("detail-title", "children"),
        ],
        [
            Input("service-table", "active_cell"),
            Input("back-button", "n_clicks"),
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

        if trigger == "back-button":
            return None, {"display": "none"}, {"display": "block"}, None

        if trigger == "service-table" and active_cell and table_data:
            row_idx = active_cell["row"]
            if row_idx < len(table_data):
                name = table_data[row_idx]["service_name"]
                rank_df_local, mapping_df_local, _ = _load_all(base_dir)
                svc_row = rank_df_local[rank_df_local["service_name"] == name].iloc[0]
                is_susp = bool(svc_row["is_suspicious"])
                accent = T["red"] if is_susp else T["cyan"]

                title = html.Div(
                    [
                        html.P("Service Detail", className="section-label", style={"marginBottom": "6px"}),
                        html.H2(
                            name,
                            style={
                                "fontFamily": T["font_mono"],
                                "fontSize": "26px",
                                "fontWeight": "600",
                                "color": accent,
                                "letterSpacing": "-0.5px",
                            },
                        ),
                    ]
                )

                content = _detail_layout(name, rank_df_local, mapping_df_local)
                return content, {"display": "block"}, {"display": "none"}, title

        raise dash.exceptions.PreventUpdate
