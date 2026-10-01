"""
Fetch the five data.gov.sg "Resale Flat Prices" datasets into data/raw/<dataset_id>.csv.

- The four pre-2017 datasets are (expected to be) static: they are pulled from the API
  too, but only re-pulled every STATIC_REFRESH_DAYS (default 7) in case data.gov.sg
  revises them. In between, the copy cached by GitHub Actions is reused. If a re-pull
  fails, the cached copy is kept instead of failing the run.
- The Jan-2017-onwards dataset is live and is re-fetched on EVERY run.

Two fetch paths are tried in order:
  1. Bulk CSV download   (api-open.data.gov.sg ... /initiate-download + /poll-download)
  2. Paged datastore API (data.gov.sg/api/action/datastore_search, as in the project brief)

The API key is read from the DATA_GOV_SG_API_KEY environment variable (a GitHub
Actions secret) -- it is never written into the repo.  Without a key the public,
lower rate limits apply.

A run fails loudly (non-zero exit) rather than publishing a partial dataset; the
previously deployed dashboard then simply stays live.
"""
import csv
import io
import os
import sys
import time

import requests

DATASETS = {
    "d_ebc5ab87086db484f88045b47411ebc5": {"label": "1990-1999 (approval date)", "static": True, "min_rows": 250_000},
    "d_43f493c6c50d54243cc1eab0df142d6a": {"label": "2000-Feb 2012 (approval date)", "static": True, "min_rows": 300_000},
    "d_2d5ff9ea31397b66239f245f57751537": {"label": "Mar 2012-Dec 2014 (registration date)", "static": True, "min_rows": 45_000},
    "d_ea9ed51da2787afaf8e51f827c304208": {"label": "Jan 2015-Dec 2016 (registration date)", "static": True, "min_rows": 30_000},
    "d_8b84c4ee58e3cfc0ece0d773c8ca6abc": {"label": "Jan 2017 onwards (registration date, LIVE)", "static": False, "min_rows": 200_000},
}
REQUIRED_COLS = {"month", "town", "flat_type", "block", "street_name", "storey_range",
                 "floor_area_sqm", "flat_model", "lease_commence_date", "resale_price"}

STATIC_REFRESH_DAYS = int(os.environ.get("STATIC_REFRESH_DAYS", "7"))
RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
API_KEY = os.environ.get("DATA_GOV_SG_API_KEY", "").strip()
HEADERS = {"x-api-key": API_KEY} if API_KEY else {}
HEADERS["User-Agent"] = "hdb-resale-dashboard/1.0 (GitHub Actions)"

session = requests.Session()
session.headers.update(HEADERS)


def get_json(url, params=None, tries=8):
    """GET with retry/back-off on rate limiting (429) and transient 5xx errors."""
    delay = 3
    for attempt in range(1, tries + 1):
        try:
            r = session.get(url, params=params, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == tries:
                raise
            print(f"    retry {attempt}/{tries - 1} in {delay}s ({e})", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 60)


# ---------------------------------------------------------------- path 1: bulk download
def fetch_bulk(dataset_id):
    base = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}"
    get_json(f"{base}/initiate-download")
    for _ in range(40):
        j = get_json(f"{base}/poll-download")
        data = j.get("data") or {}
        url = data.get("url")
        if url:
            r = requests.get(url, timeout=300)  # pre-signed URL: no API headers
            r.raise_for_status()
            text = r.content.decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
            return rows
        time.sleep(3)
    raise RuntimeError("bulk download URL never became ready")


# ---------------------------------------------------------------- path 2: datastore_search
def fetch_paged(dataset_id, limit=10_000):
    url = "https://data.gov.sg/api/action/datastore_search"
    rows, offset, total = [], 0, None
    while True:
        j = get_json(url, params={"resource_id": dataset_id, "limit": limit, "offset": offset})
        if not j.get("success", False):
            raise RuntimeError(f"datastore_search returned success=false: {str(j)[:300]}")
        res = j["result"]
        total = res.get("total", total)
        recs = res.get("records", [])
        for rec in recs:
            rec.pop("_id", None)
        rows.extend(recs)
        offset += len(recs)
        print(f"    {offset:,} / {total if total is not None else '?':,} rows", flush=True)
        if not recs or (total is not None and offset >= total):
            break
        time.sleep(0.6 if API_KEY else 2.5)  # be gentle with the rate limit
    if total is not None and len(rows) < total:
        raise RuntimeError(f"only got {len(rows)} of {total} rows")
    return rows


def validate(rows, meta):
    if not rows:
        raise RuntimeError("no rows returned")
    missing = REQUIRED_COLS - set(rows[0].keys())
    if missing:
        raise RuntimeError(f"missing columns {missing}")
    if len(rows) < meta["min_rows"]:
        raise RuntimeError(f"only {len(rows):,} rows (expected >= {meta['min_rows']:,})")


def write_csv(rows, path):
    cols = list(rows[0].keys())
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    print(f"API key present: {'yes' if API_KEY else 'NO (public rate limits)'}")
    for ds, meta in DATASETS.items():
        path = os.path.join(RAW_DIR, f"{ds}.csv")
        stamp = path + ".fetched"
        have_copy = os.path.exists(path) and os.path.getsize(path) > 1_000_000
        if meta["static"] and have_copy and os.path.exists(stamp):
            age_days = (time.time() - float(open(stamp).read().strip() or 0)) / 86400
            if age_days < STATIC_REFRESH_DAYS:
                print(f"[cached] {meta['label']} (pulled {age_days:.1f} days ago; "
                      f"re-pulled every {STATIC_REFRESH_DAYS} days)")
                continue
        print(f"[fetch]  {meta['label']}  ({ds})", flush=True)
        rows, errors = None, []
        for name, fn in (("bulk download", fetch_bulk), ("datastore_search", fetch_paged)):
            try:
                print(f"  trying {name} ...", flush=True)
                rows = fn(ds)
                validate(rows, meta)
                print(f"  OK via {name}: {len(rows):,} rows", flush=True)
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                print(f"  {name} failed: {e}", flush=True)
                rows = None
        if rows is None:
            if meta["static"] and have_copy:
                # A historical era failed to re-pull: keep the copy we already have.
                print(f"  WARNING: keeping previously fetched copy of {ds}")
                continue
            sys.exit(f"FAILED to fetch {ds}: " + " | ".join(errors))
        if meta["static"] and have_copy:
            with open(path, newline="", encoding="utf-8") as f:
                old_n = sum(1 for _ in f) - 1
            if old_n != len(rows):
                print(f"  NOTE: historical dataset changed: {old_n:,} -> {len(rows):,} rows")
        write_csv(rows, path)
        with open(stamp, "w") as f:
            f.write(str(time.time()))
    print("All datasets present.")


if __name__ == "__main__":
    main()
