from __future__ import annotations

from pathlib import Path

from boundary_analyzer.parsing.trace_reader import read_all_traces, save_spans_csv
from boundary_analyzer.settings_loader import load_settings


def main() -> int:
    settings = load_settings()

    traces_dir = Path(settings.get("output_dir", "data/raw/traces"))
    interim_dir = Path("data/interim")
    output_file = interim_dir / "spans.csv"

    print(f"Reading traces from: {traces_dir}")
    df = read_all_traces(traces_dir)

    print(f"Found {len(df)} spans")
    print(f"Services: {df['service_name'].unique().tolist()}")

    save_spans_csv(df, output_file)
    print(f"Saved to: {output_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
