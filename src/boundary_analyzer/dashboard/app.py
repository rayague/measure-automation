from __future__ import annotations

from pathlib import Path

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


def _load_service_rank() -> pd.DataFrame:
    """Load service_rank.csv."""
    path = Path("data/processed/service_rank.csv")
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _load_endpoint_table_map() -> pd.DataFrame:
    """Load endpoint_table_map.csv for detail view."""
    path = Path("data/interim/endpoint_table_map.csv")
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _create_summary_card(title: str, value: str | float, color: str, is_highlight: bool = False) -> html.Div:
    """Create a summary card with professional styling and hover effect."""
    bg_color = color
    text_color = "#1565c0" if is_highlight else "#424242"
    value_color = "#1565c0" if is_highlight else "#212121"
    
    return html.Div(
        style={
            "backgroundColor": bg_color,
            "padding": "24px",
            "borderRadius": "12px",
            "textAlign": "center",
            "minWidth": "180px",
            "flex": "1",
            "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
            "transition": "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
            "cursor": "default",
        },
        children=[
            html.H3(
                title,
                style={
                    "color": text_color,
                    "margin": "0",
                    "fontSize": "13px",
                    "fontWeight": "500",
                    "letterSpacing": "0.5px",
                    "textTransform": "uppercase",
                },
            ),
            html.H2(
                str(value),
                style={
                    "color": value_color,
                    "margin": "12px 0 0 0",
                    "fontSize": "36px",
                    "fontWeight": "700",
                },
            ),
        ],
    )


def _create_interactive_table(df: pd.DataFrame) -> dash_table.DataTable:
    """Create interactive table with click-to-navigate and professional styling."""
    if df.empty:
        return dash_table.DataTable()
    
    display_df = df[["rank", "service_name", "scom_score", "endpoints_count", "tables_count", "is_suspicious"]].copy()
    
    style_data_conditional = [
        {
            "if": {"filter_query": "{is_suspicious} = true"},
            "backgroundColor": "#ffebee",
            "color": "#c62828",
            "fontWeight": "600",
            "cursor": "pointer",
        },
        {
            "if": {"filter_query": "{is_suspicious} = false"},
            "cursor": "pointer",
        },
        {
            "if": {"row_index": "odd"},
            "backgroundColor": "rgba(0,0,0,0.02)",
        },
        {
            "if": {"column_id": "rank"},
            "textAlign": "center",
            "fontWeight": "600",
        },
        {
            "if": {"column_id": "scom_score"},
            "textAlign": "right",
            "fontWeight": "500",
        },
    ]
    
    return dash_table.DataTable(
        id="service-table",
        data=display_df.to_dict("records"),
        columns=[
            {"name": "Rank", "id": "rank"},
            {"name": "Service", "id": "service_name"},
            {"name": "SCOM", "id": "scom_score", "type": "numeric", "format": {"specifier": ".4f"}},
            {"name": "Endpoints", "id": "endpoints_count", "type": "numeric"},
            {"name": "Tables", "id": "tables_count", "type": "numeric"},
            {"name": "Status", "id": "is_suspicious", "type": "text"},
        ],
        style_data_conditional=style_data_conditional,
        style_header={
            "backgroundColor": "#1e88e5",
            "color": "white",
            "fontWeight": "600",
            "textAlign": "left",
            "padding": "12px",
        },
        style_cell={
            "textAlign": "left",
            "padding": "14px 12px",
            "fontFamily": "Arial, sans-serif",
            "fontSize": "14px",
            "border": "none",
        },
        style_table={
            "borderRadius": "8px",
            "overflow": "hidden",
            "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
        },
        page_size=8,
        row_selectable="single",
    )


def _create_service_heatmap(mapping_df: pd.DataFrame, service_name: str) -> dcc.Graph:
    """Create endpoint-table heatmap for a service with clean styling."""
    service_df = mapping_df[mapping_df["service_name"] == service_name].copy()
    
    if service_df.empty:
        return dcc.Graph()
    
    pivot = service_df.pivot_table(
        index="endpoint_key",
        columns="table",
        values="count",
        fill_value=0,
    )
    
    fig = px.imshow(
        pivot,
        labels={"x": "Database Table", "y": "Endpoint", "color": "Usage"},
        x=pivot.columns,
        y=pivot.index,
        color_continuous_scale="Blues",
        title="Endpoint-Table Access Pattern",
    )
    
    fig.update_layout(
        xaxis_title=None,
        yaxis_title=None,
        coloraxis_colorbar_title="Count",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Arial, sans-serif"},
        margin=dict(t=50, b=50, l=50, r=50),
    )
    
    fig.update_xaxes(tickangle=45, tickfont={"size": 10})
    fig.update_yaxes(tickfont={"size": 10})
    
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


def _create_service_detail_page(service_name: str, rank_df: pd.DataFrame, mapping_df: pd.DataFrame) -> html.Div:
    """Create service detail page with storytelling and explanation."""
    service_data = rank_df[rank_df["service_name"] == service_name]
    
    if service_data.empty:
        return html.Div("Service not found", style={"textAlign": "center", "padding": "50px"})
    
    row = service_data.iloc[0]
    is_suspicious = row["is_suspicious"]
    
    status_color = "#e53935" if is_suspicious else "#43a047"
    status_text = "Suspicious" if is_suspicious else "Healthy"
    explanation = (
        "This service has low cohesion. Endpoints share few tables, suggesting the boundary may need review."
        if is_suspicious
        else "This service has good cohesion. Endpoints share related tables, indicating a well-defined boundary."
    )
    
    return html.Div(
        style={"padding": "0"},
        children=[
            html.Div(
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "justifyContent": "space-between",
                    "marginBottom": "24px",
                },
                children=[
                    html.H2(
                        service_name,
                        style={
                            "color": "#1e88e5",
                            "margin": "0",
                            "fontSize": "28px",
                            "fontWeight": "700",
                        },
                    ),
                    html.Button(
                        "← Back to Overview",
                        id="back-button",
                        n_clicks=0,
                        style={
                            "backgroundColor": "#1e88e5",
                            "color": "white",
                            "border": "none",
                            "padding": "10px 24px",
                            "borderRadius": "6px",
                            "cursor": "pointer",
                            "fontSize": "14px",
                            "fontWeight": "500",
                            "transition": "all 0.2s ease",
                        },
                    ),
                ],
            ),
            
            html.Div(
                style={
                    "backgroundColor": status_color if is_suspicious else "#e8f5e9",
                    "padding": "20px",
                    "borderRadius": "8px",
                    "marginBottom": "24px",
                    "borderLeft": "4px solid status_color",
                },
                children=[
                    html.H3(
                        f"Status: {status_text}",
                        style={
                            "color": status_color if is_suspicious else "#2e7d32",
                            "margin": "0 0 8px 0",
                            "fontSize": "18px",
                        },
                    ),
                    html.P(
                        explanation,
                        style={
                            "color": "#424242",
                            "margin": "0",
                            "fontSize": "14px",
                            "lineHeight": "1.5",
                        },
                    ),
                ],
            ),
            
            html.Div(
                style={"display": "flex", "gap": "16px", "marginBottom": "32px", "flexWrap": "wrap"},
                children=[
                    _create_summary_card("SCOM Score", f"{row['scom_score']:.4f}", "#e3f2fd", True),
                    _create_summary_card("Rank", f"#{row['rank']}", "#e8f5e9"),
                    _create_summary_card("Endpoints", row["endpoints_count"], "#fff3e0"),
                    _create_summary_card("Tables", row["tables_count"], "#f3e5f5"),
                ],
            ),
            
            html.H3(
                "Endpoint-Table Access Pattern",
                style={"color": "#424242", "fontSize": "18px", "marginBottom": "16px"},
            ),
            _create_service_heatmap(mapping_df, service_name),
        ],
    )


def create_app() -> dash.Dash:
    """Create the enhanced Dash application with multi-page navigation and storytelling."""
    app = dash.Dash(__name__)
    
    rank_df = _load_service_rank()
    mapping_df = _load_endpoint_table_map()
    
    summary = create_summary_cards(rank_df)
    
    app.layout = html.Div(
        style={
            "padding": "0",
            "fontFamily": "Arial, sans-serif",
            "backgroundColor": "#fafafa",
            "minHeight": "100vh",
        },
        children=[
            dcc.Store(id="page-state", data="overview"),
            dcc.Store(id="selected-service", data=None),
            
            html.Div(
                style={
                    "backgroundColor": "linear-gradient(135deg, #1e88e5 0%, #1565c0 100%)",
                    "padding": "32px 40px",
                    "boxShadow": "0 4px 12px rgba(0,0,0,0.1)",
                },
                children=[
                    html.H1(
                        "Microservice Boundary Analyzer",
                        style={
                            "color": "white",
                            "margin": "0",
                            "fontSize": "32px",
                            "fontWeight": "700",
                            "letterSpacing": "-0.5px",
                        },
                    ),
                    html.P(
                        "Analyze service cohesion and detect problematic microservice boundaries",
                        style={
                            "color": "rgba(255,255,255,0.9)",
                            "margin": "8px 0 0 0",
                            "fontSize": "14px",
                            "fontWeight": "400",
                        },
                    ),
                ],
            ),
            
            html.Div(
                style={"padding": "32px 40px", "maxWidth": "1400px", "margin": "0 auto"},
                children=[
                    html.Div(id="overview-page", children=[
                        html.Div(
                            style={
                                "display": "flex",
                                "gap": "16px",
                                "marginBottom": "32px",
                                "flexWrap": "wrap",
                            },
                            children=[
                                _create_summary_card("Total Services", summary["total_services"], "#e3f2fd"),
                                _create_summary_card("Suspicious", summary["suspicious_count"], "#ffebee", True),
                                _create_summary_card("Healthy", summary["safe_count"], "#e8f5e9"),
                                _create_summary_card("Avg SCOM", f"{summary['avg_scom']:.3f}", "#e3f2fd", True),
                            ],
                        ),
                        
                        html.Div(
                            style={
                                "backgroundColor": "white",
                                "padding": "24px",
                                "borderRadius": "12px",
                                "boxShadow": "0 2px 8px rgba(0,0,0,0.06)",
                                "marginBottom": "24px",
                            },
                            children=[
                                html.H3(
                                    "Cohesion Score Distribution",
                                    style={
                                        "color": "#424242",
                                        "fontSize": "18px",
                                        "margin": "0 0 16px 0",
                                    },
                                ),
                                create_scom_distribution(rank_df),
                            ],
                        ),
                        
                        html.Div(
                            style={
                                "backgroundColor": "white",
                                "padding": "24px",
                                "borderRadius": "12px",
                                "boxShadow": "0 2px 8px rgba(0,0,0,0.06)",
                                "marginBottom": "24px",
                            },
                            children=[
                                html.H3(
                                    "Service Cohesion Scores (Animated)",
                                    style={
                                        "color": "#424242",
                                        "fontSize": "18px",
                                        "margin": "0 0 16px 0",
                                    },
                                ),
                                create_animated_bar_chart(rank_df),
                            ],
                        ),
                        
                        html.Div(
                            style={
                                "backgroundColor": "white",
                                "padding": "24px",
                                "borderRadius": "12px",
                                "boxShadow": "0 2px 8px rgba(0,0,0,0.06)",
                            },
                            children=[
                                html.H3(
                                    "Service Ranking (Click any row for details)",
                                    style={
                                        "color": "#424242",
                                        "fontSize": "18px",
                                        "margin": "0 0 16px 0",
                                    },
                                ),
                                _create_interactive_table(rank_df),
                            ],
                        ),
                    ]),
                    
                    html.Div(id="detail-page", style={"display": "none"}),
                ],
            ),
        ],
    )
    
    @app.callback(
        [Output("detail-page", "children"),
         Output("detail-page", "style"),
         Output("overview-page", "style")],
        [Input("service-table", "active_cell"),
         Input("back-button", "n_clicks")],
        [State("service-table", "data"),
         State("detail-page", "style")],
    )
    def update_page(active_cell, back_clicks, table_data, detail_style):
        ctx = dash.callback_context
        
        if not ctx.triggered:
            raise dash.exceptions.PreventUpdate
        
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        
        if trigger_id == "back-button":
            return None, {"display": "none"}, {"display": "block"}
        
        if trigger_id == "service-table" and active_cell:
            row_idx = active_cell["row"]
            if table_data and row_idx < len(table_data):
                service_name = table_data[row_idx]["service_name"]
                detail_content = _create_service_detail_page(service_name, rank_df, mapping_df)
                return detail_content, {"display": "block"}, {"display": "none"}
        
        raise dash.exceptions.PreventUpdate
    
    return app


def main() -> int:
    """Run the enhanced dashboard."""
    app = create_app()
    print("Starting enhanced dashboard on http://127.0.0.1:8050")
    app.run_server(host="127.0.0.1", port=8050, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
