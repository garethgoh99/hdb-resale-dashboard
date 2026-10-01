"""
v3: expands the per-record schema from 9 fields (which only had all four of
mean/median/min/max for PRICE, and just one statistic each for lease/area/psf) to 18
fields -- mean, median, min AND max for all four dimensions (price, lease, area, psf).
This is what lets the dashboard offer a single global "statistic" toggle (median �
default, or mean, lowest, highest) that applies uniformly to every number the dashboard
shows, not just price.

Usage: python3 aggregate.py /path/to/harmonized.csv [output_dir]

record = [
  year, n,
  price_mean, price_median, price_min, price_max,
  lease_mean, lease_median, lease_min, lease_max,
  area_mean,  area_median,  area_min,  area_max,
  psf_mean,   psf_median,   psf_min,   psf_max,
]  (18 fields)
"""
import sys
import os
import json
import pandas as pd

REQUIRED_COLS = {"town", "street_name", "flat_type", "year", "resale_price",
                  "floor_area_sqm", "remaining_lease_yrs", "psf"}

DIMS = [("price", "resale_price"), ("lease", "remaining_lease_yrs"),
        ("area", "floor_area_sqm"), ("psf", "psf")]
STATS = ["mean", "median", "min", "max"]


def make_agg(df, group_cols):
    agg_spec = {"n": ("resale_price", "size")}
    for dim, col in DIMS:
        for stat in STATS:
            agg_spec[f"{dim}_{stat}"] = (col, stat)
    g = df.groupby(group_cols).agg(**agg_spec).reset_index()
    # Round for compact, readable JSON. Price/lease/area/psf all get 1 decimal place
    # except price (whole dollars) and n (integer).
    for dim, _ in DIMS:
        for stat in STATS:
            col = f"{dim}_{stat}"
            if dim == "price":
                g[col] = g[col].round(0).astype(int)
            else:
                g[col] = g[col].round(1)
    return g


def to_record(row):
    rec = [int(row.year), int(row.n)]
    for dim, _ in DIMS:
        for stat in STATS:
            v = getattr(row, f"{dim}_{stat}")
            rec.append(int(v) if dim == "price" else float(v))
    return rec


def main(path, out_dir="."):
    os.makedirs(out_dir, exist_ok=True)
    df = pd.read_csv(path)
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise SystemExit(f"Missing required columns: {missing}. Run hdb-data-ingestion first.")

    df["flat_type"] = df["flat_type"].str.strip()

    # ============ per flat_type (for the chart's flat-type breakdown) ============
    grp_s = make_agg(df, ["town", "street_name", "flat_type", "year"])
    street_data = {}
    for row in grp_s.itertuples(index=False):
        rec = to_record(row)
        street_data.setdefault(row.town, {}).setdefault(row.street_name, {}) \
            .setdefault(row.flat_type, []).append(rec)

    grp_t = make_agg(df, ["town", "flat_type", "year"])
    town_agg = {}
    for row in grp_t.itertuples(index=False):
        rec = to_record(row)
        town_agg.setdefault(row.town, {}).setdefault(row.flat_type, []).append(rec)

    grp_n = make_agg(df, ["flat_type", "year"])
    sg_agg = {}
    for row in grp_n.itertuples(index=False):
        rec = to_record(row)
        sg_agg.setdefault(row.flat_type, []).append(rec)

    latest_year = int(grp_s["year"].max())
    town_summary = {}
    for town, g in grp_s.groupby("town"):
        latest = g[g["year"] == latest_year]
        town_summary[town] = {
            "streets": int(g["street_name"].nunique()),
            "count": int(g["n"].sum()),
            "avg_price_latest": int(latest["price_mean"].mean()) if len(latest) else None,
        }

    # ============ ALL flat types combined, per year (for range stats/CAGR/ranking) ============
    grp_ty = make_agg(df, ["town", "year"])
    town_year = {}
    for row in grp_ty.itertuples(index=False):
        town_year.setdefault(row.town, []).append(to_record(row))
    for t in town_year:
        town_year[t].sort(key=lambda r: r[0])

    grp_sy = make_agg(df, ["town", "street_name", "year"])
    street_year = {}
    for row in grp_sy.itertuples(index=False):
        street_year.setdefault(row.town, {}).setdefault(row.street_name, []).append(to_record(row))
    for t in street_year:
        for s in street_year[t]:
            street_year[t][s].sort(key=lambda r: r[0])

    grp_sgy = make_agg(df, ["year"])
    sg_year = sorted([to_record(row) for row in grp_sgy.itertuples(index=False)], key=lambda r: r[0])

    # legacy latest-year snapshot, kept for backward compatibility / quick reference
    gty = df.groupby(["town", "year"]).agg(
        n=("resale_price", "size"), mean_price=("resale_price", "mean"),
        median_price=("resale_price", "median"), mean_area=("floor_area_sqm", "mean"),
        mean_lease=("remaining_lease_yrs", "mean"), median_psf=("psf", "median"),
    ).reset_index()
    town_compare = {}
    for town, g in gty.groupby("town"):
        ly = int(g["year"].max())
        latest = g[g["year"] == ly].iloc[0]
        town_compare[town] = {
            "latest_year": ly,
            "latest_price": int(round(latest["mean_price"])),
            "latest_median_price": int(round(latest["median_price"])),
            "latest_psf": round(float(latest["median_psf"]), 1),
            "latest_area": round(float(latest["mean_area"]), 1),
            "latest_lease": round(float(latest["mean_lease"]), 1),
            "total_n": int(g["n"].sum()),
        }

    outputs = {
        "street_data.json": street_data, "town_agg.json": town_agg,
        "sg_agg.json": sg_agg, "town_summary.json": town_summary,
        "town_compare.json": town_compare,
        "town_year.json": town_year, "street_year.json": street_year, "sg_year.json": sg_year,
    }
    for fname, obj in outputs.items():
        fpath = os.path.join(out_dir, fname)
        with open(fpath, "w") as f:
            json.dump(obj, f, separators=(",", ":"))
        print(f"Wrote {fpath} ({len(json.dumps(obj))/1024:.1f} KB)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python3 aggregate.py /path/to/harmonized.csv [output_dir]")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(sys.argv[1]))
    main(sys.argv[1], out_dir)
