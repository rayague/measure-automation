from __future__ import annotations


def _with_alpha(color: str, alpha: float) -> str:
    c = str(color).strip()
    if c.startswith("rgba("):
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
        ri = int(c[1:3], 16)
        gi = int(c[3:5], 16)
        bi = int(c[5:7], 16)
        return f"rgba({ri},{gi},{bi},{alpha})"
    return c


T = {
    "bg_base": "#06080f",
    "bg_card": "rgba(14, 22, 38, 0.85)",
    "bg_card2": "rgba(18, 28, 50, 0.70)",
    "bg_header": "rgba(6, 8, 15, 0.95)",
    "cyan": "#00e5ff",
    "cyan_dim": "rgba(0, 229, 255, 0.12)",
    "cyan_glow": "rgba(0, 229, 255, 0.35)",
    "amber": "#ff9800",
    "amber_dim": "rgba(255, 152, 0, 0.12)",
    "amber_glow": "rgba(255, 152, 0, 0.35)",
    "green": "#00e676",
    "green_dim": "rgba(0, 230, 118, 0.10)",
    "red": "#ff1744",
    "red_dim": "rgba(255, 23, 68, 0.12)",
    "text_primary": "#e8f0fe",
    "text_secondary": "rgba(200, 220, 255, 0.55)",
    "text_muted": "rgba(200, 220, 255, 0.30)",
    "border": "rgba(0, 229, 255, 0.10)",
    "border_hot": "rgba(0, 229, 255, 0.40)",
    "font_display": "'Syne', 'DM Sans', sans-serif",
    "font_mono": "'JetBrains Mono', 'Fira Code', monospace",
}

GLOBAL_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@300;400;600&display=swap');

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --cyan:      {T["cyan"]};
  --amber:     {T["amber"]};
  --green:     {T["green"]};
  --red:       {T["red"]};
  --bg:        {T["bg_base"]};
  --card:      {T["bg_card"]};
  --border:    {T["border"]};
  --text:      {T["text_primary"]};
  --text2:     {T["text_secondary"]};
}}

html, body {{
  background: {T["bg_base"]};
  color: {T["text_primary"]};
  font-family: {T["font_display"]};
  min-height: 100vh;
  overflow-x: hidden;
}}

::-webkit-scrollbar {{ width: 6px; background: {T["bg_base"]}; }}
::-webkit-scrollbar-thumb {{ background: {T["border_hot"]}; border-radius: 3px; }}

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

.dash-card {{
  position: relative;
  background: {T["bg_card"]};
  border: 1px solid {T["border"]};
  border-radius: 16px;
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  overflow: hidden;
  transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.3s ease;
}}
.dash-card:hover {{
  border-color: {T["border_hot"]};
  box-shadow: 0 0 40px {T["cyan_glow"]}, 0 20px 60px rgba(0,0,0,0.4);
  transform: translateY(-2px);
}}
.dash-card::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, {T["cyan"]}, transparent);
  opacity: 0.5;
}}

.metric-card {{
  position: relative;
  background: {T["bg_card"]};
  border: 1px solid {T["border"]};
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
.metric-card--cyan {{ border-color: {T["cyan_glow"]}; }}
.metric-card--cyan::after {{
  content: '';
  position: absolute;
  bottom: -30px; right: -30px;
  width: 120px; height: 120px;
  background: radial-gradient(circle, {T["cyan_dim"]}, transparent 70%);
  pointer-events: none;
}}
.metric-card--amber {{ border-color: {T["amber_glow"]}; }}
.metric-card--amber::after {{
  content: '';
  position: absolute;
  bottom: -30px; right: -30px;
  width: 120px; height: 120px;
  background: radial-gradient(circle, {T["amber_dim"]}, transparent 70%);
  pointer-events: none;
}}
.metric-card--green {{ border-color: rgba(0,230,118,0.35); }}
.metric-card--green::after {{
  content: '';
  position: absolute;
  bottom: -30px; right: -30px;
  width: 120px; height: 120px;
  background: radial-gradient(circle, {T["green_dim"]}, transparent 70%);
  pointer-events: none;
}}

.section-label {{
  font-family: {T["font_mono"]};
  font-size: 10px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: {T["text_muted"]};
  margin-bottom: 14px;
}}

.title-accent {{
  display: inline-block;
  position: relative;
}}
.title-accent::after {{
  content: '';
  position: absolute;
  bottom: -4px; left: 0;
  width: 100%; height: 2px;
  background: linear-gradient(90deg, {T["cyan"]}, transparent);
}}

@keyframes pulse-ring {{
  0%   {{ box-shadow: 0 0 0 0 rgba(255,23,68,0.6); }}
  70%  {{ box-shadow: 0 0 0 10px rgba(255,23,68,0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(255,23,68,0); }}
}}
.pulse-dot {{
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: {T["red"]};
  animation: pulse-ring 1.8s infinite;
  margin-right: 8px;
  vertical-align: middle;
}}

@keyframes fadeSlideUp {{
  from {{ opacity: 0; transform: translateY(16px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}
.fade-in {{
  animation: fadeSlideUp 0.45s cubic-bezier(0.22, 1, 0.36, 1) both;
}}

.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner td {{
  font-family: {T["font_mono"]} !important;
  font-size: 12.5px !important;
  border-bottom: 1px solid {T["border"]} !important;
  background: transparent !important;
  color: {T["text_primary"]} !important;
  padding: 13px 16px !important;
  transition: background 0.15s;
}}
.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner tr:hover td {{
  background: rgba(0,229,255,0.04) !important;
}}
.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner th {{
  font-family: {T["font_mono"]} !important;
  font-size: 10px !important;
  letter-spacing: 2px !important;
  text-transform: uppercase !important;
  background: rgba(0,229,255,0.05) !important;
  color: {T["cyan"]} !important;
  border-bottom: 1px solid {T["border_hot"]} !important;
  padding: 12px 16px !important;
}}
.dash-table-container {{
  border: 1px solid {T["border"]};
  border-radius: 12px;
  overflow: hidden;
}}

.back-btn {{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: {T["font_mono"]};
  font-size: 12px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: {T["cyan"]};
  background: {T["cyan_dim"]};
  border: 1px solid {T["cyan_glow"]};
  padding: 10px 20px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}}
.back-btn:hover {{
  background: {T["cyan"]};
  color: {T["bg_base"]};
  box-shadow: 0 0 20px {T["cyan_glow"]};
}}

@keyframes scan {{
  0%   {{ top: -4px; }}
  100% {{ top: 100%; }}
}}
"""

PLOT_LAYOUT: dict[str, Any] = dict(
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
