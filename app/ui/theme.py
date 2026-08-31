import streamlit as st


def apply_theme():
    """Inject modern styling into the Streamlit app."""
    st.markdown(
        """
        <style>
        /* Modern font & styling */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }

        code, pre, [data-testid="stCodeBlock"] {
            font-family: 'JetBrains Mono', monospace !important;
        }

        /* Hero banner & Card containers */
        .sql-hero-card {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.9) 100%);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(12px);
        }

        .sql-card {
            background: rgba(30, 41, 59, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            padding: 18px 20px;
            margin-bottom: 16px;
            transition: all 0.2s ease-in-out;
            backdrop-filter: blur(8px);
        }

        .sql-card:hover {
            border-color: rgba(99, 102, 241, 0.4);
            transform: translateY(-2px);
            box-shadow: 0 8px 20px -4px rgba(99, 102, 241, 0.15);
        }

        .sql-stat-val {
            font-size: 1.8rem;
            font-weight: 700;
            background: linear-gradient(90deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 2px;
        }

        .sql-stat-label {
            font-size: 0.85rem;
            color: #94a3b8;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* Pill badges */
        .badge-pill {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.025em;
        }
        .badge-success { background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.3); }
        .badge-warning { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-danger  { background: rgba(239, 68, 68, 0.15);  color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
        .badge-info    { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
        .badge-purple  { background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }

        /* Metric cards */
        [data-testid="stMetricValue"] {
            font-weight: 700 !important;
        }

        /* Sidebar styling */
        [data-testid="stSidebar"] {
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }

        /* Modern tab styling */
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 8px 8px 0 0;
            padding: 8px 16px;
            font-weight: 500;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_metric_card(label: str, value: str, subtext: str = "", badge: str = "", badge_type: str = "info"):
    """Helper to render a sleek custom metric card."""
    badge_html = f'<span class="badge-pill badge-{badge_type}">{badge}</span>' if badge else ""
    st.markdown(
        f"""
        <div class="sql-card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                <span class="sql-stat-label">{label}</span>
                {badge_html}
            </div>
            <div class="sql-stat-val">{value}</div>
            <div style="font-size:0.8rem; color:#94a3b8; margin-top:4px;">{subtext}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
