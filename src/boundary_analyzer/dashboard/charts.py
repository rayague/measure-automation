from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import dcc


def create_animated_bar_chart(df: pd.DataFrame) -> dcc.Graph:
    """Create animated bar chart of SCOM scores with smooth transitions."""
    if df.empty:
        return dcc.Graph()
    
    df_sorted = df.sort_values("rank")
    
    fig = px.bar(
        df_sorted,
        x="service_name",
        y="scom_score",
        color="is_suspicious",
        color_discrete_map={True: "#e53935", False: "#1e88e5"},
        title="Service Cohesion Scores",
        labels={
            "service_name": "Service",
            "scom_score": "SCOM Score",
            "is_suspicious": "Status"
        },
        animation_frame="rank",
        animation_group="service_name",
        range_y=[0, 1.1],
    )
    
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="SCOM Score",
        showlegend=True,
        hovermode="x unified",
        transition={"duration": 800, "easing": "cubic-in-out"},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Arial, sans-serif", "size": 12},
        margin=dict(t=50, b=50, l=50, r=50),
    )
    
    fig.update_xaxes(
        showgrid=False,
        tickangle=45,
        tickfont={"size": 10},
    )
    
    fig.update_yaxes(
        gridcolor="rgba(0,0,0,0.05)",
        range=[0, 1],
        dtick=0.2,
    )
    
    fig.update_traces(
        hovertemplate="<b>%{x}</b><br>SCOM: %{y:.3f}<br>Status: %{customdata[0]}",
        customdata=df_sorted[["is_suspicious"]],
        marker_line_width=0,
    )
    
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


def create_scom_distribution(df: pd.DataFrame) -> dcc.Graph:
    """Create histogram of SCOM score distribution with threshold."""
    if df.empty:
        return dcc.Graph()
    
    fig = px.histogram(
        df,
        x="scom_score",
        nbins=15,
        title="Cohesion Score Distribution",
        labels={"scom_score": "SCOM Score"},
        color_discrete_sequence=["#1e88e5"],
    )
    
    fig.update_layout(
        xaxis_title="SCOM Score",
        yaxis_title="Number of Services",
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Arial, sans-serif"},
        margin=dict(t=50, b=50, l=50, r=50),
    )
    
    fig.update_xaxes(gridcolor="rgba(0,0,0,0.05)", range=[0, 1], dtick=0.2)
    fig.update_yaxes(gridcolor="rgba(0,0,0,0.05)")
    
    fig.add_vline(
        x=0.5,
        line_dash="dash",
        line_color="#e53935",
        line_width=2,
        annotation_text="Suspicious Threshold",
        annotation_position="top right",
        annotation_font={"color": "#e53935", "size": 11},
    )
    
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


def create_summary_cards(df: pd.DataFrame) -> dict:
    """Create summary statistics for cards."""
    if df.empty:
        return {
            "total_services": 0,
            "suspicious_count": 0,
            "safe_count": 0,
            "avg_scom": 0.0,
            "min_scom": 0.0,
            "max_scom": 0.0,
        }
    
    suspicious_count = len(df[df["is_suspicious"] == True])
    safe_count = len(df[df["is_suspicious"] == False])
    
    return {
        "total_services": len(df),
        "suspicious_count": suspicious_count,
        "safe_count": safe_count,
        "avg_scom": df["scom_score"].mean(),
        "min_scom": df["scom_score"].min(),
        "max_scom": df["scom_score"].max(),
    }
