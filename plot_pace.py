"""Plot pace per 100m vs length number for each swimming activity."""

import json
from pathlib import Path

import plotly.graph_objects as go

DATA_DIR = Path("data")


def load_lengths(filepath: Path) -> list[dict]:
    """Extract individual lengths from an activity file."""
    with open(filepath) as f:
        data = json.load(f)

    lengths = []
    for lap in data.get("splits", {}).get("lapDTOs", []):
        for length in lap.get("lengthDTOs", []):
            if length.get("distance", 0) > 0 and length.get("duration", 0) > 0:
                lengths.append(length)
    return lengths


def pace_per_100m(distance: float, duration: float) -> float:
    """Convert distance (m) and duration (s) to pace in seconds per 100m."""
    return (duration / distance) * 100


def main():
    fig = go.Figure()

    activity_files = sorted(DATA_DIR.glob("swim_[0-9]*.json"))
    if not activity_files:
        print("No activity files found in data/. Run download_swim_data.py first.")
        return

    for filepath in activity_files:
        lengths = load_lengths(filepath)
        if not lengths:
            continue

        # Extract date from filename (swim_ID_DATE.json)
        date_str = filepath.stem.split("_")[-1]

        length_numbers = list(range(1, len(lengths) + 1))
        paces = [pace_per_100m(l["distance"], l["duration"]) for l in lengths]

        # Format pace as mm:ss for hover
        hover_text = []
        for i, (p, l) in enumerate(zip(paces, lengths)):
            mins, secs = divmod(int(p), 60)
            stroke = l.get("swimStroke", "")
            hover_text.append(
                f"Length {i+1}<br>Pace: {mins}:{secs:02d}/100m<br>"
                f"Stroke: {stroke}<br>Strokes: {l.get('totalNumberOfStrokes', 'N/A')}"
            )

        fig.add_trace(go.Scatter(
            x=length_numbers,
            y=paces,
            mode="lines+markers",
            name=date_str,
            hovertext=hover_text,
            hoverinfo="text",
            marker=dict(size=4),
            line=dict(width=1.5),
        ))

    # Format y-axis as mm:ss
    max_pace = max(max(t.y) for t in fig.data)
    min_pace = min(min(t.y) for t in fig.data)
    tick_step = 10
    tick_vals = list(range(int(min_pace // tick_step) * tick_step,
                           int(max_pace) + tick_step + 1, tick_step))
    tick_text = [f"{v // 60:.0f}:{v % 60:02.0f}" for v in tick_vals]

    fig.update_layout(
        title="Swimming Pace per 100m by Length",
        xaxis_title="Length #",
        yaxis_title="Pace (min:sec / 100m)",
        yaxis=dict(
            tickvals=tick_vals,
            ticktext=tick_text,
            autorange="reversed",  # lower pace (faster) at top
        ),
        hovermode="closest",
        template="plotly_white",
    )

    output = DATA_DIR / "pace_chart.html"
    fig.write_html(str(output))
    print(f"Chart saved to {output}")
    fig.show()


if __name__ == "__main__":
    main()
