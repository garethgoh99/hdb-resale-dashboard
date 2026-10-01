"""
Aggregate build/harmonized.csv and render the self-contained dashboard to site/index.html.

Usage: python3 pipeline/build.py
"""
import datetime as dt
import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
BUILD = os.path.join(ROOT, "build")
SITE = os.path.join(ROOT, "site")
sys.path.insert(0, HERE)
import aggregate  # noqa: E402


def main():
    harmonized = os.path.join(BUILD, "harmonized.csv")
    agg_dir = os.path.join(BUILD, "agg")
    aggregate.main(harmonized, agg_dir)

    def load(name):
        with open(os.path.join(agg_dir, name)) as f:
            return json.load(f)

    street_data, town_agg, sg_agg = load("street_data.json"), load("town_agg.json"), load("sg_agg.json")
    town_summary, town_year = load("town_summary.json"), load("town_year.json")
    street_year, sg_year = load("street_year.json"), load("sg_year.json")

    with open(os.path.join(ROOT, "data", "town_coords.json")) as f:
        all_coords = json.load(f)
    towns_in_data = set(town_year)
    no_coords = sorted(towns_in_data - set(all_coords))
    if no_coords:
        print(f"WARNING: towns with no map coordinates (not drawn on map, still in tables): {no_coords}")
    coords = {t: c for t, c in all_coords.items() if t in towns_in_data}

    df = pd.read_csv(harmonized, usecols=["month", "town", "street_name"])
    sgt = dt.timezone(dt.timedelta(hours=8))
    subs = {
        "__EARLIEST_MONTH__": df["month"].min(),
        "__LATEST_MONTH__": df["month"].max(),
        "__TOTAL_TX__": f"{len(df):,}",
        "__TOTAL_TOWNS__": str(df["town"].nunique()),
        "__TOTAL_STREETS__": f"{df.groupby(['town', 'street_name']).ngroups:,}",
        "__BUILT_AT__": dt.datetime.now(sgt).strftime("%d %b %Y, %H:%M SGT"),
    }
    js = lambda o: json.dumps(o, separators=(",", ":"))  # noqa: E731
    data_subs = {
        "__STREET_DATA_JSON__": js(street_data), "__TOWN_AGG_JSON__": js(town_agg),
        "__SG_AGG_JSON__": js(sg_agg), "__TOWN_SUMMARY_JSON__": js(town_summary),
        "__TOWN_COORDS_JSON__": js(coords), "__TOWN_YEAR_JSON__": js(town_year),
        "__STREET_YEAR_JSON__": js(street_year), "__SG_YEAR_JSON__": js(sg_year),
    }

    with open(os.path.join(ROOT, "template", "dashboard_template.html")) as f:
        html = f.read()
    # Data JSON first (it never contains "__X__" tokens), then the text tokens.
    for k, v in data_subs.items():
        if k not in html:
            sys.exit(f"Template is missing placeholder {k}")
        html = html.replace(k, v)
    for k, v in subs.items():
        html = html.replace(k, v)
    leftover = set(re.findall(r"__[A-Z_]+__", html))
    if leftover:
        sys.exit(f"Unfilled placeholders: {leftover}")

    os.makedirs(SITE, exist_ok=True)
    out = os.path.join(SITE, "index.html")
    with open(out, "w") as f:
        f.write(html)
    with open(os.path.join(SITE, "meta.json"), "w") as f:
        json.dump({k.strip("_").lower(): v for k, v in subs.items()}, f, indent=1)
    open(os.path.join(SITE, ".nojekyll"), "w").close()
    print(f"Wrote {out} ({os.path.getsize(out)/1e6:.1f} MB) | data {subs['__EARLIEST_MONTH__']} -> "
          f"{subs['__LATEST_MONTH__']} | {subs['__TOTAL_TX__']} transactions")


if __name__ == "__main__":
    main()
