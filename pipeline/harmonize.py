"""
Harmonize every CSV in data/raw/ (the five data.gov.sg resale-price eras) into one
clean dataframe -> build/harmonized.csv.  Logic follows the hdb-data-ingestion skill,
made robust to the API returning every field as a string.

Usage: python3 pipeline/harmonize.py [raw_dir] [out_csv]
"""
import glob
import os
import re
import sys

import pandas as pd

SQM_TO_SQFT = 10.7639
HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "raw")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "build", "harmonized.csv")


def parse_lease(v):
    """'61 years 04 months' -> 61.33 ; '70' / 70 -> 70.0 ; blank -> NaN."""
    if pd.isna(v):
        return float("nan")
    s = str(v).strip()
    if not s:
        return float("nan")
    try:
        return float(s)  # 2015-2016 era: plain year count (possibly as text via the API)
    except ValueError:
        pass
    y = re.search(r"(\d+)\s*year", s)
    m = re.search(r"(\d+)\s*month", s)
    if not y and not m:
        return float("nan")
    return round((int(y.group(1)) if y else 0) + (int(m.group(1)) if m else 0) / 12, 2)


def harmonize_one(path):
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    for c in ("town", "street_name", "flat_type", "block", "storey_range", "flat_model", "month"):
        df[c] = df[c].astype(str).str.strip()
    df["town"] = df["town"].str.upper()
    df["street_name"] = df["street_name"].str.upper()
    df["flat_type"] = df["flat_type"].str.upper().replace({"MULTI GENERATION": "MULTI-GENERATION"})
    df["month"] = df["month"].str[:7]
    df["year"] = pd.to_numeric(df["month"].str[:4], errors="coerce")
    df["resale_price"] = pd.to_numeric(df["resale_price"], errors="coerce")
    df["floor_area_sqm"] = pd.to_numeric(df["floor_area_sqm"], errors="coerce")
    df["lease_commence_date"] = pd.to_numeric(df["lease_commence_date"], errors="coerce")

    if "remaining_lease" in df.columns:
        df["remaining_lease_yrs"] = df["remaining_lease"].map(parse_lease)
    else:
        df["remaining_lease_yrs"] = float("nan")
    # Eras without the column (or blank cells): standard 99-year lease approximation.
    fallback = 99 - (df["year"] - df["lease_commence_date"])
    df["remaining_lease_yrs"] = df["remaining_lease_yrs"].fillna(fallback)

    bad = df[["year", "resale_price", "floor_area_sqm"]].isna().any(axis=1) | (df["floor_area_sqm"] <= 0)
    if bad.any():
        print(f"  dropping {int(bad.sum())} unparseable rows")
    df = df[~bad].copy()
    df["year"] = df["year"].astype(int)
    df["psf"] = df["resale_price"] / (df["floor_area_sqm"] * SQM_TO_SQFT)

    keep = ["month", "town", "flat_type", "block", "street_name", "storey_range",
            "floor_area_sqm", "flat_model", "lease_commence_date", "resale_price",
            "year", "remaining_lease_yrs", "psf"]
    return df[keep]


def main():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))
    if not files:
        sys.exit(f"No CSVs in {RAW_DIR} -- run pipeline/fetch.py first.")
    frames = []
    for p in files:
        f = harmonize_one(p)
        print(f"{os.path.basename(p)}: {len(f):,} rows, {f['month'].min()} -> {f['month'].max()}")
        frames.append(f)
    # Era overlap: the files are meant to tile the timeline month-by-month. If two files
    # ever cover the same month, keep that month from the LATER era file only. (A row-level
    # drop_duplicates would also delete genuine distinct sales that happen to share
    # street/type/area/price in one month, so it isn't used.)
    frames.sort(key=lambda f: f["month"].min())
    for i in range(len(frames) - 1):
        later_months = set().union(*[set(f["month"].unique()) for f in frames[i + 1:]])
        overlap = frames[i]["month"].isin(later_months)
        if overlap.any():
            print(f"Era overlap: dropping {int(overlap.sum()):,} rows from an earlier file "
                  f"for months also present in a later file")
            frames[i] = frames[i][~overlap]
    df = pd.concat(frames, ignore_index=True)
    print(f"Combined: {len(df):,} rows | {df['town'].nunique()} towns | "
          f"{df['month'].min()} -> {df['month'].max()}")
    print("flat_types:", sorted(df["flat_type"].unique()))
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
