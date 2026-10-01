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


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def q_records(g):
    """make_agg output (grouped incl. 'qkey') -> 18-field records with field 0 = year + (q-1)/4."""
    out = []
    for row in g.itertuples(index=False):
        rec = [float(row.qkey), int(row.n)]
        for dim, _ in aggregate.DIMS:
            for stat in aggregate.STATS:
                v = getattr(row, f"{dim}_{stat}")
                rec.append(int(v) if dim == "price" else float(v))
        out.append(rec)
    return sorted(out, key=lambda r: r[0])


def build_quarterly(harmonized, out_dir):
    """Per-quarter aggregates, same 18-field schema, one lazily-loaded JSON per town + sg.json.
    Lets the detail view switch to a quarterly timescale for short year ranges without
    bloating the main page."""
    df = pd.read_csv(harmonized)
    m = df["month"].str[5:7].astype(int)
    df["qkey"] = df["year"] + ((m - 1) // 3) / 4
    os.makedirs(out_dir, exist_ok=True)

    sg = {"byFt": {ft: q_records(sub) for ft, sub in aggregate.make_agg(df, ["flat_type", "qkey"]).groupby("flat_type")},
          "comb": q_records(aggregate.make_agg(df, ["qkey"]))}
    with open(os.path.join(out_dir, "sg.json"), "w") as f:
        json.dump(sg, f, separators=(",", ":"))
    index = {}
    for town, tdf in df.groupby("town"):
        t_ft = aggregate.make_agg(tdf, ["flat_type", "qkey"])
        t_c = aggregate.make_agg(tdf, ["qkey"])
        s_ft = aggregate.make_agg(tdf, ["street_name", "flat_type", "qkey"])
        s_c = aggregate.make_agg(tdf, ["street_name", "qkey"])
        streets = {}
        for (st, ft), sub in s_ft.groupby(["street_name", "flat_type"]):
            streets.setdefault(st, {"byFt": {}, "comb": []})["byFt"][ft] = q_records(sub)
        for st, sub in s_c.groupby("street_name"):
            streets[st]["comb"] = q_records(sub)
        obj = {"town": {"byFt": {ft: q_records(sub) for ft, sub in t_ft.groupby("flat_type")},
                        "comb": q_records(t_c)},
               "streets": streets}
        fn = slug(town) + ".json"
        index[town] = fn
        with open(os.path.join(out_dir, fn), "w") as f:
            json.dump(obj, f, separators=(",", ":"))
    total = sum(os.path.getsize(os.path.join(out_dir, x)) for x in os.listdir(out_dir))
    print(f"Wrote quarterly data: {len(index)} town files + sg.json ({total/1e6:.1f} MB total)")
    return index


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

    q_index = build_quarterly(harmonized, os.path.join(SITE, "q"))

    df = pd.read_csv(harmonized, usecols=["month", "town", "street_name"])
    fetch_log = {}
    try:
        with open(os.path.join(BUILD, "fetch_log.json")) as f:
            fetch_log = json.load(f)
    except (OSError, ValueError):
        print("NOTE: no build/fetch_log.json (local build?) -- using build time as pull time")
    sgt = dt.timezone(dt.timedelta(hours=8))
    subs = {
        "__EARLIEST_MONTH__": df["month"].min(),
        "__LATEST_MONTH__": df["month"].max(),
        "__TOTAL_TX__": f"{len(df):,}",
        "__TOTAL_TOWNS__": str(df["town"].nunique()),
        "__TOTAL_STREETS__": f"{df.groupby(['town', 'street_name']).ngroups:,}",
        "__BUILT_AT__": dt.datetime.now(sgt).strftime("%d %b %Y, %H:%M SGT"),
        "__LATEST_MONTH_LONG__": dt.datetime.strptime(df["month"].max(), "%Y-%m").strftime("%b %Y"),
    }
    def sgt_fmt(iso):
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(sgt).strftime("%d %b %Y, %H:%M SGT")
    subs["__API_PULLED_AT__"] = sgt_fmt(fetch_log["live_pulled_at"]) if fetch_log.get("live_pulled_at") else subs["__BUILT_AT__"]
    src_upd = fetch_log.get("source_last_updated")
    try:
        src_txt = sgt_fmt(src_upd) if src_upd else ""
    except ValueError:
        src_txt = str(src_upd)
    subs["__SOURCE_NOTE__"] = f" · data.gov.sg last updated the dataset {src_txt}" if src_txt else ""
    js = lambda o: json.dumps(o, separators=(",", ":"))  # noqa: E731
    data_subs = {
        "__STREET_DATA_JSON__": js(street_data), "__TOWN_AGG_JSON__": js(town_agg),
        "__SG_AGG_JSON__": js(sg_agg), "__TOWN_SUMMARY_JSON__": js(town_summary),
        "__TOWN_COORDS_JSON__": js(coords), "__TOWN_YEAR_JSON__": js(town_year),
        "__STREET_YEAR_JSON__": js(street_year), "__SG_YEAR_JSON__": js(sg_year),
        "__Q_INDEX_JSON__": js(q_index),
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
