"""SwimApp — Dash application for swimming data analysis."""

import logging
import os
from datetime import date, datetime, timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html
from dotenv import load_dotenv

import db

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = dash.Dash(__name__, title="SwimApp")

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _build_layout():
    db.init_db()
    categories = db.get_categories()
    cat_options = [{"label": "All", "value": "all"}] + [
        {"label": c, "value": c} for c in categories
    ]

    # Date range defaults
    activities = db.get_activities()
    if not activities.empty:
        min_date = activities["date"].min()
        max_date = activities["date"].max()
    else:
        min_date = "2025-01-01"
        max_date = date.today().isoformat()

    return html.Div(className="app-container", children=[
        # Sidebar
        html.Div(className="sidebar", children=[
            html.H2("SwimApp"),

            html.Div(className="sidebar-section", children=[
                html.Label("Category"),
                dcc.Dropdown(
                    id="category-dropdown",
                    options=cat_options,
                    value="all",
                    clearable=False,
                ),
            ]),

            html.Div(className="sidebar-section", children=[
                html.Label("Date Range"),
                dcc.DatePickerRange(
                    id="date-range",
                    min_date_allowed=min_date,
                    max_date_allowed=max_date,
                    start_date=min_date,
                    end_date=max_date,
                    display_format="YYYY-MM-DD",
                ),
                html.Div(id="calendar-highlights", style={"fontSize": "0.8rem", "marginTop": "6px", "color": "#6c757d"}),
            ]),

            html.Div(className="sidebar-section", children=[
                html.Label("View Mode"),
                dcc.RadioItems(
                    id="view-mode",
                    options=[
                        {"label": "Performance Over Time", "value": "over_time"},
                        {"label": "Within-Swim Profile", "value": "in_swim"},
                    ],
                    value="over_time",
                    labelStyle={"display": "block", "marginBottom": "4px"},
                ),
            ]),

            html.Div(className="sidebar-section", children=[
                html.Label("Y-Axis"),
                dcc.RadioItems(
                    id="y-axis-toggle",
                    options=[
                        {"label": "Pace (min:ss/100m)", "value": "pace"},
                        {"label": "Speed (m/s)", "value": "speed"},
                    ],
                    value="pace",
                    labelStyle={"display": "block", "marginBottom": "4px"},
                ),
            ]),

            # In-swim specific controls
            html.Div(id="in-swim-controls", style={"display": "none"}, children=[
                html.Div(className="sidebar-section", children=[
                    html.Label("X-Axis"),
                    dcc.RadioItems(
                        id="x-axis-toggle",
                        options=[
                            {"label": "Length #", "value": "length"},
                            {"label": "Elapsed Time", "value": "time"},
                        ],
                        value="length",
                        labelStyle={"display": "block", "marginBottom": "4px"},
                    ),
                ]),

                html.Div(className="sidebar-section", children=[
                    dcc.Checklist(
                        id="avg-toggle",
                        options=[{"label": " Show average + deviation", "value": "show_avg"}],
                        value=[],
                    ),
                ]),

                html.Div(className="sidebar-section", children=[
                    html.Label("Top N Fastest"),
                    html.Div(style={"display": "flex", "gap": "8px", "alignItems": "center"}, children=[
                        dcc.Input(
                            id="top-n-input",
                            type="number",
                            min=1,
                            max=50,
                            value=5,
                            style={"width": "60px"},
                        ),
                        html.Button("Apply", id="top-n-button", n_clicks=0),
                    ]),
                ]),
            ]),

            html.Div(className="sidebar-section", children=[
                html.Label("Time of Day"),
                dcc.RadioItems(
                    id="time-of-day",
                    options=[
                        {"label": "All", "value": "all"},
                        {"label": "Morning (<12:00)", "value": "morning"},
                        {"label": "Afternoon (>=12:00)", "value": "afternoon"},
                    ],
                    value="all",
                    labelStyle={"display": "block", "marginBottom": "4px"},
                ),
            ]),
        ]),

        # Main content
        html.Div(className="main-content", children=[
            dcc.Graph(id="main-chart", style={"height": "60vh"}),
            html.Div(id="stats-panel", className="stats-panel"),
        ]),
    ])


app.layout = _build_layout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_pace(seconds_per_100m: float) -> str:
    mins, secs = divmod(int(seconds_per_100m), 60)
    return f"{mins}:{secs:02d}"


def _filter_activities(category, start_date, end_date, time_of_day):
    cat = None if category == "all" else category
    df = db.get_activities(category=cat, start_date=start_date, end_date=end_date)
    if time_of_day == "morning":
        df = df[df["time_of_day_minutes"] < 720]
    elif time_of_day == "afternoon":
        df = df[df["time_of_day_minutes"] >= 720]
    return df


def _make_stats(df, y_col):
    if df.empty:
        return []
    cards = []

    def _card(label, value):
        return html.Div(className="stat-card", children=[
            html.Div(label, className="stat-label"),
            html.Div(value, className="stat-value"),
        ])

    if y_col == "avg_pace_100m":
        cards.append(_card("Average", _format_pace(df[y_col].mean())))
        cards.append(_card("Fastest", _format_pace(df[y_col].min())))
        cards.append(_card("Slowest", _format_pace(df[y_col].max())))
        std = df[y_col].std()
        cards.append(_card("Std Dev", f"{std:.1f}s" if pd.notna(std) else "—"))
    else:
        cards.append(_card("Average", f"{df[y_col].mean():.2f} m/s"))
        cards.append(_card("Fastest", f"{df[y_col].max():.2f} m/s"))
        cards.append(_card("Slowest", f"{df[y_col].min():.2f} m/s"))
        std = df[y_col].std()
        cards.append(_card("Std Dev", f"{std:.3f}" if pd.notna(std) else "—"))

    cards.append(_card("Sessions", str(len(df))))
    return cards


def _make_length_stats(lengths_df, y_col):
    if lengths_df.empty:
        return []
    cards = []

    def _card(label, value):
        return html.Div(className="stat-card", children=[
            html.Div(label, className="stat-label"),
            html.Div(value, className="stat-value"),
        ])

    if y_col == "pace_100m":
        cards.append(_card("Average", _format_pace(lengths_df[y_col].mean())))
        cards.append(_card("Fastest", _format_pace(lengths_df[y_col].min())))
        cards.append(_card("Slowest", _format_pace(lengths_df[y_col].max())))
        std = lengths_df[y_col].std()
        cards.append(_card("Std Dev", f"{std:.1f}s" if pd.notna(std) else "—"))
    else:
        cards.append(_card("Average", f"{lengths_df[y_col].mean():.2f} m/s"))
        cards.append(_card("Fastest", f"{lengths_df[y_col].max():.2f} m/s"))
        cards.append(_card("Slowest", f"{lengths_df[y_col].min():.2f} m/s"))
        std = lengths_df[y_col].std()
        cards.append(_card("Std Dev", f"{std:.3f}" if pd.notna(std) else "—"))

    n_activities = lengths_df["activity_id"].nunique()
    cards.append(_card("Sessions", str(n_activities)))
    return cards


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


# Show/hide in-swim controls
@callback(
    Output("in-swim-controls", "style"),
    Input("view-mode", "value"),
)
def toggle_in_swim_controls(view_mode):
    if view_mode == "in_swim":
        return {"display": "block"}
    return {"display": "none"}


# Calendar highlights
@callback(
    Output("calendar-highlights", "children"),
    Input("category-dropdown", "value"),
)
def update_calendar_highlights(category):
    cat = None if category == "all" else category
    dates = db.get_activity_dates(cat)
    if not dates:
        return "No activities found."
    return f"{len(dates)} sessions available"


# Main chart + stats
@callback(
    Output("main-chart", "figure"),
    Output("stats-panel", "children"),
    Input("view-mode", "value"),
    Input("category-dropdown", "value"),
    Input("date-range", "start_date"),
    Input("date-range", "end_date"),
    Input("y-axis-toggle", "value"),
    Input("x-axis-toggle", "value"),
    Input("time-of-day", "value"),
    Input("avg-toggle", "value"),
    Input("top-n-button", "n_clicks"),
    State("top-n-input", "value"),
)
def update_chart(view_mode, category, start_date, end_date, y_axis, x_axis,
                 time_of_day, avg_toggle, top_n_clicks, top_n_value):
    if view_mode == "over_time":
        return _build_over_time(category, start_date, end_date, y_axis, time_of_day)
    else:
        show_avg = "show_avg" in (avg_toggle or [])
        use_top_n = top_n_clicks > 0
        top_n = int(top_n_value) if top_n_value else 5
        return _build_in_swim(
            category, start_date, end_date, y_axis, x_axis,
            time_of_day, show_avg, use_top_n, top_n,
        )


# ---------------------------------------------------------------------------
# Performance Over Time
# ---------------------------------------------------------------------------


def _build_over_time(category, start_date, end_date, y_axis, time_of_day):
    df = _filter_activities(category, start_date, end_date, time_of_day)
    fig = go.Figure()

    if df.empty:
        fig.update_layout(
            title="No data for selected filters",
            template="plotly_white",
        )
        return fig, []

    y_col = "avg_pace_100m" if y_axis == "pace" else "avg_speed_ms"
    y_label = "Pace (min:ss / 100m)" if y_axis == "pace" else "Speed (m/s)"

    # Hover text
    hover = []
    for _, r in df.iterrows():
        pace_str = _format_pace(r["avg_pace_100m"])
        hover.append(
            f"{r['date']}<br>{r['category']}<br>"
            f"Distance: {r['total_distance_m']:.0f}m<br>"
            f"Pace: {pace_str}/100m<br>"
            f"Speed: {r['avg_speed_ms']:.2f} m/s"
        )

    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df[y_col],
        mode="markers",
        name="Sessions",
        marker=dict(size=8),
        hovertext=hover,
        hoverinfo="text",
    ))

    # Rolling average trend line
    if len(df) >= 5:
        rolling = df[y_col].rolling(window=5, min_periods=3).mean()
        fig.add_trace(go.Scatter(
            x=df["date"],
            y=rolling,
            mode="lines",
            name="Trend (5-session avg)",
            line=dict(width=2, dash="dash"),
        ))

    # Format y-axis for pace
    if y_axis == "pace":
        y_vals = df[y_col].dropna()
        if not y_vals.empty:
            tick_step = 5
            mn, mx = int(y_vals.min() // tick_step) * tick_step, int(y_vals.max()) + tick_step + 1
            tick_vals = list(range(mn, mx, tick_step))
            tick_text = [_format_pace(v) for v in tick_vals]
            fig.update_yaxes(
                tickvals=tick_vals,
                ticktext=tick_text,
                autorange="reversed",
            )

    fig.update_layout(
        title="Performance Over Time",
        xaxis_title="Date",
        yaxis_title=y_label,
        template="plotly_white",
        hovermode="closest",
    )

    stats = _make_stats(df, y_col)
    return fig, stats


# ---------------------------------------------------------------------------
# Within-Swim Profile
# ---------------------------------------------------------------------------


def _build_in_swim(category, start_date, end_date, y_axis, x_axis,
                   time_of_day, show_avg, use_top_n, top_n):
    df = _filter_activities(category, start_date, end_date, time_of_day)
    fig = go.Figure()

    if df.empty:
        fig.update_layout(title="No data for selected filters", template="plotly_white")
        return fig, []

    # If top-N mode, pick fastest by avg_pace_100m
    if use_top_n and top_n > 0:
        df = df.nsmallest(min(top_n, len(df)), "avg_pace_100m")

    activity_ids = df["activity_id"].tolist()
    all_lengths = db.get_lengths(activity_ids)

    if all_lengths.empty:
        fig.update_layout(title="No length data available", template="plotly_white")
        return fig, []

    y_col = "pace_100m" if y_axis == "pace" else "speed_ms"
    y_label = "Pace (min:ss / 100m)" if y_axis == "pace" else "Speed (m/s)"

    # Build x values per activity
    def _get_x(group):
        if x_axis == "time":
            return group["duration_s"].cumsum().tolist()
        return group["length_index"].tolist()

    # Individual traces
    for aid in activity_ids:
        group = all_lengths[all_lengths["activity_id"] == aid].copy()
        act_row = df[df["activity_id"] == aid].iloc[0]
        act_date = act_row["date"]

        x_vals = _get_x(group)
        y_vals = group[y_col].tolist()

        opacity = 0.25 if show_avg else 1.0
        fig.add_trace(go.Scatter(
            x=x_vals,
            y=y_vals,
            mode="lines+markers" if not show_avg else "lines",
            name=act_date,
            opacity=opacity,
            marker=dict(size=3),
            line=dict(width=1.5 if not show_avg else 1),
            hovertext=[
                f"{act_date} — Length {int(r.length_index)}<br>"
                f"Pace: {_format_pace(r.pace_100m)}/100m<br>"
                f"Speed: {r.speed_ms:.2f} m/s<br>"
                f"Stroke: {r.stroke_type or '—'}"
                for _, r in group.iterrows()
            ],
            hoverinfo="text",
        ))

    # Average + deviation
    if show_avg and len(activity_ids) > 1:
        # Align by length_index
        max_len = all_lengths["length_index"].max()
        avg_y = []
        std_upper = []
        std_lower = []
        x_range = list(range(1, int(max_len) + 1))

        for idx in x_range:
            vals = all_lengths[all_lengths["length_index"] == idx][y_col]
            if len(vals) > 0:
                m = vals.mean()
                s = vals.std() if len(vals) > 1 else 0
                avg_y.append(m)
                std_upper.append(m + s)
                std_lower.append(m - s)
            else:
                avg_y.append(None)
                std_upper.append(None)
                std_lower.append(None)

        # Std deviation band
        fig.add_trace(go.Scatter(
            x=x_range + x_range[::-1],
            y=std_upper + std_lower[::-1],
            fill="toself",
            fillcolor="rgba(0,100,200,0.15)",
            line=dict(color="rgba(0,0,0,0)"),
            name="Std Dev",
            showlegend=True,
            hoverinfo="skip",
        ))

        # Average line
        fig.add_trace(go.Scatter(
            x=x_range,
            y=avg_y,
            mode="lines",
            name="Average",
            line=dict(color="rgb(0,100,200)", width=3),
        ))

    # Format y-axis for pace
    if y_axis == "pace":
        y_vals = all_lengths[y_col].dropna()
        if not y_vals.empty:
            tick_step = 5
            mn = int(y_vals.min() // tick_step) * tick_step
            mx = int(y_vals.max()) + tick_step + 1
            tick_vals = list(range(mn, mx, tick_step))
            tick_text = [_format_pace(v) for v in tick_vals]
            fig.update_yaxes(
                tickvals=tick_vals,
                ticktext=tick_text,
                autorange="reversed",
            )

    x_label = "Length #" if x_axis == "length" else "Elapsed Time (s)"
    title = "Within-Swim Profile"
    if use_top_n:
        title += f" (Top {min(top_n, len(df))} Fastest)"

    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        template="plotly_white",
        hovermode="closest",
    )

    stats = _make_length_stats(all_lengths, y_col)
    return fig, stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def startup_sync():
    """Run sync and processing on startup."""
    try:
        from sync import sync_activities
        from processing import process_all_new

        logger.info("Syncing with Garmin Connect...")
        new_ids = sync_activities()
        logger.info("Synced %d new activities.", len(new_ids))

        logger.info("Processing activities...")
        n = process_all_new()
        logger.info("Processed %d new activities.", n)
    except Exception:
        logger.exception("Startup sync failed — continuing with existing data.")


if __name__ == "__main__":
    startup_sync()

    debug = os.getenv("DASH_DEBUG", "false").lower() == "true"
    port = int(os.getenv("PORT", "8050"))
    app.run(debug=debug, port=port)
