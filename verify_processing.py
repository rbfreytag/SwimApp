"""Generate verification charts for processing pipeline and both views."""

import json
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

import db


def _format_pace(seconds_per_100m: float) -> str:
    mins, secs = divmod(int(seconds_per_100m), 60)
    return f"{mins}:{secs:02d}"


def main():
    db.init_db()
    activities = db.get_activities()
    if activities.empty:
        print("No activities in DB. Run processing.py first.")
        return

    # --- Chart 1: Processing verification ---
    fig1 = make_subplots(
        rows=2, cols=1,
        subplot_titles=[
            "Activities by Date (coloured by category)",
            "Length Correction Example (2026-03-06)",
        ],
        vertical_spacing=0.15,
    )

    for cat in sorted(activities["category"].unique()):
        subset = activities[activities["category"] == cat]
        fig1.add_trace(
            go.Scatter(
                x=subset["date"],
                y=subset["total_distance_m"],
                mode="markers",
                name=cat,
                marker=dict(size=8),
                hovertext=[
                    f"{r.date}<br>{r.category}<br>{r.total_distance_m:.0f}m<br>"
                    f"Pace: {_format_pace(r.avg_pace_100m)}/100m"
                    for _, r in subset.iterrows()
                ],
                hoverinfo="text",
            ),
            row=1, col=1,
        )

    fig1.update_yaxes(title_text="Distance (m)", row=1, col=1)

    raw_path = Path("data/raw/swim_22078432919_2026-03-06.json")
    if raw_path.exists():
        with open(raw_path) as f:
            data = json.load(f)

        raw_durs = []
        for lap in data.get("splits", {}).get("lapDTOs", []):
            for length in lap.get("lengthDTOs", []):
                if length.get("distance", 0) > 0:
                    raw_durs.append(length["duration"])

        fig1.add_trace(
            go.Scatter(
                x=list(range(1, len(raw_durs) + 1)),
                y=raw_durs, mode="lines+markers",
                name="Raw", marker=dict(size=4), line=dict(color="red"),
            ),
            row=2, col=1,
        )

        corrected_df = db.get_lengths(["22078432919"])
        fig1.add_trace(
            go.Scatter(
                x=corrected_df["length_index"].tolist(),
                y=corrected_df["duration_s"].tolist(),
                mode="lines+markers", name="Corrected",
                marker=dict(size=4, color=["orange" if c else "blue" for c in corrected_df["is_corrected"]]),
                line=dict(color="blue"),
            ),
            row=2, col=1,
        )

    fig1.update_yaxes(title_text="Duration (s)", row=2, col=1)
    fig1.update_xaxes(title_text="Length #", row=2, col=1)
    fig1.update_layout(height=800, template="plotly_white", title_text="Processing Verification")
    fig1.write_html("data/verify_processing.html")
    print("Saved data/verify_processing.html")

    # --- Chart 2: Performance Over Time (1500m category) ---
    df_1500 = db.get_activities(category="1500m")
    if not df_1500.empty:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=df_1500["date"], y=df_1500["avg_pace_100m"],
            mode="markers", name="Sessions", marker=dict(size=6),
            hovertext=[
                f"{r.date}<br>Pace: {_format_pace(r.avg_pace_100m)}/100m<br>"
                f"Distance: {r.total_distance_m:.0f}m"
                for _, r in df_1500.iterrows()
            ],
            hoverinfo="text",
        ))
        if len(df_1500) >= 5:
            rolling = df_1500["avg_pace_100m"].rolling(window=5, min_periods=3).mean()
            fig2.add_trace(go.Scatter(
                x=df_1500["date"], y=rolling,
                mode="lines", name="Trend (5-session avg)",
                line=dict(width=2, dash="dash"),
            ))

        y_vals = df_1500["avg_pace_100m"].dropna()
        tick_step = 5
        mn = int(y_vals.min() // tick_step) * tick_step
        mx = int(y_vals.max()) + tick_step + 1
        tick_vals = list(range(mn, mx, tick_step))
        fig2.update_yaxes(tickvals=tick_vals, ticktext=[_format_pace(v) for v in tick_vals], autorange="reversed")
        fig2.update_layout(
            title="Performance Over Time — 1500m Category",
            xaxis_title="Date", yaxis_title="Pace (min:ss / 100m)",
            template="plotly_white",
        )
        fig2.write_html("data/verify_over_time.html")
        print("Saved data/verify_over_time.html")

    # --- Chart 3: Within-Swim Profile (last 10 sessions, 1500m) ---
    if not df_1500.empty:
        recent = df_1500.tail(10)
        fig3 = go.Figure()

        for _, r in recent.iterrows():
            lengths = db.get_lengths([r["activity_id"]])
            if lengths.empty:
                continue
            fig3.add_trace(go.Scatter(
                x=lengths["length_index"].tolist(),
                y=lengths["pace_100m"].tolist(),
                mode="lines+markers", name=r["date"],
                marker=dict(size=3), line=dict(width=1.5),
                hovertext=[
                    f"{r['date']} — Length {int(row.length_index)}<br>"
                    f"Pace: {_format_pace(row.pace_100m)}/100m"
                    for _, row in lengths.iterrows()
                ],
                hoverinfo="text",
            ))

        y_vals = []
        for t in fig3.data:
            y_vals.extend(t.y)
        if y_vals:
            mn = int(min(y_vals) // 5) * 5
            mx = int(max(y_vals)) + 6
            tick_vals = list(range(mn, mx, 5))
            fig3.update_yaxes(tickvals=tick_vals, ticktext=[_format_pace(v) for v in tick_vals], autorange="reversed")

        fig3.update_layout(
            title="Within-Swim Profile — Last 10 x 1500m",
            xaxis_title="Length #", yaxis_title="Pace (min:ss / 100m)",
            template="plotly_white", hovermode="closest",
        )
        fig3.write_html("data/verify_in_swim.html")
        print("Saved data/verify_in_swim.html")


if __name__ == "__main__":
    main()
