#!/usr/bin/env python3
"""Example charts from a parsed EFIS-D100 CSV (see parse.py / README.md).

Usage:
    python charts.py 2026-06-27.csv            # write PNGs to ./charts/
    python charts.py 2026-06-27.csv -o out/    # custom output dir
    python charts.py 2026-06-27.csv --show     # also open interactive windows

Requires (not part of the parser's runtime deps):
    pip install pandas matplotlib

The script is unit-agnostic: it finds each field by its name prefix and reads
the unit from the CSV header, so it works whether you parsed with the default
RAW units or with -m / -i / -c. Axis labels show whatever unit is in the file.
"""

import argparse
from pathlib import Path

import matplotlib

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# 1. Load and prepare the data
# ----------------------------------------------------------------------------
def col(df: pd.DataFrame, prefix: str) -> str:
    """Return the full column name starting with `prefix` (e.g. 'pitch')."""
    for c in df.columns:
        if c == prefix or c.startswith(prefix + " ("):
            return c
    raise KeyError(f"no column starting with {prefix!r}; have {list(df.columns)}")


def airspeed_ms(df: pd.DataFrame) -> pd.Series:
    """Airspeed as a m/s Series, converting from whatever unit the header has."""
    c = col(df, "airspeed")
    unit = c[c.find("(") + 1:c.rfind(")")].strip() if "(" in c else ""
    factor = {
        "1/10 m/s": 0.1,   # RAW
        "m/s": 1.0,
        "km/h": 1 / 3.6,   # METRIC / CUSTOM
        "knots": 0.514444,  # IMPERIAL
    }.get(unit)
    if factor is None:
        raise ValueError(f"unknown airspeed unit {unit!r} in column {c!r}")
    return df[c].fillna(0) * factor


def load(csv_path: str) -> pd.DataFrame:
    # Empty cells in the multiplexed columns become NaN automatically.
    df = pd.read_csv(csv_path)

    # --- A continuous time axis -------------------------------------------
    # hour/minute/second come from the RTC; the "frame" column is a free-
    # running 1/64 s counter that is NOT phase-locked to the second boundary
    # (see docs/FORMAT.md). So do NOT build time from frame/64 + seconds.
    # Frames are contiguous and emitted at a steady 64 Hz with no gaps, so the
    # robust elapsed-time axis is simply the row index divided by 64.
    df["t_min"] = (df.index / 64.0) / 60.0   # elapsed minutes from start

    # --- The two multiplexed pairs ----------------------------------------
    # bit 0 of the status bitmask selects displayed-alt + turn-rate OR
    # pressure-alt + VSI each frame, so each of these columns is only ~50%
    # filled. For continuous lines, interpolate across the gaps; each stream
    # is still sampled at ~32 Hz, plenty for smooth plots.
    for prefix in ("altitude_displayed", "altitude_pressure",
                   "vertical_speed", "turn_rate"):
        c = col(df, prefix)
        df[c] = df[c].interpolate()

    return df


# ----------------------------------------------------------------------------
# 2. Individual charts
# ----------------------------------------------------------------------------
def chart_altitude_airspeed(df, outdir):
    """Altitude and airspeed vs time on a shared x-axis (twin y-axes)."""
    alt, spd = col(df, "altitude_pressure"), col(df, "airspeed")
    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.plot(df["t_min"], df[alt], color="tab:blue", lw=0.8)
    ax1.set_xlabel("Elapsed time (min)")
    ax1.set_ylabel(alt, color="tab:blue")
    ax1.tick_params(axis="y", labelcolor="tab:blue")

    ax2 = ax1.twinx()
    ax2.plot(df["t_min"], df[spd], color="tab:red", lw=0.6, alpha=0.8)
    ax2.set_ylabel(spd, color="tab:red")
    ax2.tick_params(axis="y", labelcolor="tab:red")

    ax1.set_title("Altitude & airspeed over time")
    fig.tight_layout()
    _save(fig, outdir, "altitude_airspeed.png")


def chart_attitude(df, outdir):
    """Pitch, roll, heading stacked in a shared-x multi-panel figure."""
    pitch, roll, yaw = col(df, "pitch"), col(df, "roll"), col(df, "yaw")
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(df["t_min"], df[pitch], color="tab:green", lw=0.6)
    axes[0].set_ylabel(pitch)
    axes[0].axhline(0, color="grey", lw=0.5)

    axes[1].plot(df["t_min"], df[roll], color="tab:purple", lw=0.6)
    axes[1].set_ylabel(roll)
    axes[1].axhline(0, color="grey", lw=0.5)

    axes[2].plot(df["t_min"], df[yaw], color="tab:orange", lw=0.4)
    axes[2].set_ylabel(yaw)
    axes[2].set_xlabel("Elapsed time (min)")
    axes[2].set_ylim(0, 360)

    axes[0].set_title("Attitude over time")
    fig.tight_layout()
    _save(fig, outdir, "attitude.png")


def chart_vertical(df, outdir):
    """Vertical speed and vertical-g, useful for spotting climbs/manoeuvres."""
    vsi, vg = col(df, "vertical_speed"), col(df, "vertical_g")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    ax1.plot(df["t_min"], df[vsi], color="tab:blue", lw=0.6)
    ax1.axhline(0, color="grey", lw=0.5)
    ax1.set_ylabel(vsi)
    ax1.set_title("Vertical speed & vertical g")

    ax2.plot(df["t_min"], df[vg], color="tab:red", lw=0.6)
    ax2.set_ylabel(vg)
    ax2.set_xlabel("Elapsed time (min)")
    fig.tight_layout()
    _save(fig, outdir, "vertical.png")


def chart_g_histogram(df, outdir):
    """Distribution of vertical-g load — a histogram (statistical chart)."""
    vg = col(df, "vertical_g")
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(df[vg].dropna(), bins=40, color="tab:red", alpha=0.8)
    ax.set_xlabel(vg)
    ax.set_ylabel("Frame count")
    ax.set_title("Vertical-g load distribution")
    fig.tight_layout()
    _save(fig, outdir, "g_histogram.png")


def chart_heading_rose(df, outdir):
    """How much time was spent on each heading — a polar histogram."""
    yaw = col(df, "yaw")
    fig = plt.figure(figsize=(7, 7))
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)  # clockwise, like a compass
    rad = np.deg2rad(df[yaw].dropna())
    bins = np.linspace(0, 2 * np.pi, 37)  # 36 wedges of 10°
    ax.hist(rad, bins=bins, color="tab:orange", alpha=0.8)
    ax.set_title("Time spent on each heading")
    _save(fig, outdir, "heading_rose.png")


def chart_envelope(df, outdir):
    """Airspeed vs altitude scatter, coloured by time — flight envelope."""
    spd, alt = col(df, "airspeed"), col(df, "altitude_pressure")
    fig, ax = plt.subplots(figsize=(10, 6))
    # Decimate for a readable scatter (every 16th frame ≈ 4 Hz).
    s = df.iloc[::16]
    sc = ax.scatter(s[spd], s[alt], c=s["t_min"], cmap="viridis", s=4)
    ax.set_xlabel(spd)
    ax.set_ylabel(alt)
    ax.set_title("Flight envelope (colour = elapsed minutes)")
    fig.colorbar(sc, ax=ax, label="Elapsed time (min)")
    fig.tight_layout()
    _save(fig, outdir, "envelope.png")


def chart_ground_track(df, outdir):
    """Dead-reckoned ground track from heading + airspeed.

    There is NO GPS in the EFIS serial stream, so this is not a real map. We
    integrate the velocity vector frame-by-frame: distance per frame is
    airspeed / 64 Hz, pointed along the heading (0=N, 90=E). Caveats:
      * Uses airspeed, not ground speed, and heading, not track — so wind is
        ignored and the path drifts from the true route.
      * No GPS anchor, so absolute position is unknown; (0,0) is just the
        start point and North is up.
    Good for the *shape* of the flight (circuits, legs, turns), not navigation.
    """
    speed = airspeed_ms(df)                  # m/s, full-rate, no gaps
    hdg = np.deg2rad(df[col(df, "yaw")])
    step = speed / 64.0                       # metres travelled per frame
    east = np.cumsum(step * np.sin(hdg)) / 1000.0   # km, +x = East
    north = np.cumsum(step * np.cos(hdg)) / 1000.0  # km, +y = North

    alt = col(df, "altitude_pressure")  # interpolated in load()
    fig, ax = plt.subplots(figsize=(9, 9))
    s = slice(None, None, 8)  # decimate the drawn points for speed
    sc = ax.scatter(east[s], north[s], c=df[alt][s], cmap="viridis", s=3)
    ax.plot(east.iloc[0], north.iloc[0], "o", color="lime", ms=12,
            mec="black", label="start", zorder=5)
    ax.plot(east.iloc[-1], north.iloc[-1], "s", color="red", ms=11,
            mec="black", label="end", zorder=5)
    ax.set_aspect("equal")  # don't distort the path's shape
    ax.set_xlabel("East displacement (km)")
    ax.set_ylabel("North displacement (km)")
    ax.set_title("Dead-reckoned ground track (no GPS — approximate)")
    ax.legend(loc="best")
    fig.colorbar(sc, ax=ax, label=alt)
    fig.tight_layout()
    _save(fig, outdir, "ground_track.png")


# ----------------------------------------------------------------------------
# Helpers / entry point
# ----------------------------------------------------------------------------
def _save(fig, outdir, name):
    # Extension comes from rcParams["savefig.format"]; resolution from
    # rcParams["savefig.dpi"] (ignored for vector formats like svg/pdf).
    path = Path(outdir) / f"{Path(name).stem}.{plt.rcParams['savefig.format']}"
    fig.savefig(path)
    print(f"wrote {path}")


def main():
    p = argparse.ArgumentParser(description="Chart a parsed EFIS-D100 CSV.")
    p.add_argument("csv", help="parsed CSV from parse.py")
    p.add_argument("-o", "--outdir", default="charts", help="output directory")
    p.add_argument("--dpi", type=int, default=200, help="output resolution (dots per inch)")
    p.add_argument("--format", default="png",
                   choices=["png", "svg", "pdf", "jpg", "webp"],
                   help="output file format (svg/pdf are vector, dpi-independent)")
    p.add_argument("--show", action="store_true", help="also open windows")
    args = p.parse_args()

    if not args.show:
        matplotlib.use("Agg")  # headless: write files without a display
    plt.rcParams["savefig.dpi"] = args.dpi
    plt.rcParams["savefig.format"] = args.format

    Path(args.outdir).mkdir(exist_ok=True)
    df = load(args.csv)

    chart_altitude_airspeed(df, args.outdir)
    chart_attitude(df, args.outdir)
    chart_vertical(df, args.outdir)
    chart_g_histogram(df, args.outdir)
    chart_heading_rose(df, args.outdir)
    chart_envelope(df, args.outdir)
    chart_ground_track(df, args.outdir)

    if args.show:
        plt.show()


# Imported after argparse so main() can call matplotlib.use("Agg") before
# pyplot selects a backend when running headless.
import matplotlib.pyplot as plt  # noqa: E402

if __name__ == "__main__":
    main()
