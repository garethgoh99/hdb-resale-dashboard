# HDB Resale Price Explorer

An interactive dashboard of Singapore HDB resale prices from 1990 to now, broken down by town, street, flat type and year. It rebuilds itself every day from data.gov.sg and is served on GitHub Pages.

**Live dashboard:** `https://<your-github-username>.github.io/<repo-name>/`

## How it stays up to date

A GitHub Actions workflow (`.github/workflows/refresh-dashboard.yml`) runs every day at 02:17 SGT. It also runs on every push to `main`, and whenever you press **Actions → Refresh dashboard → Run workflow**. Each run does the following:

1. **`pipeline/fetch.py`** pulls the five data.gov.sg "Resale Flat Prices" datasets.
   - The Jan-2017-onwards dataset (`d_8b84c4ee58e3cfc0ece0d773c8ca6abc`) is re-pulled on every run.
   - The four pre-2017 datasets are pulled from the API too, but only re-pulled once a week in case data.gov.sg ever revises them. In between, the copy in the Actions cache is reused, and if a weekly re-pull fails, the previous copy is kept.
   - The script tries the bulk-download API first and falls back to the paged `datastore_search` API.
   - It refuses to continue if a dataset comes back empty, short or with missing columns.
2. **`pipeline/harmonize.py`** merges the five eras into one schema. This covers the lease-string parsing, the `MULTI GENERATION` spelling fix, the 99-year-lease fallback and $psf.
3. **`pipeline/build.py`** aggregates the data into the 18-field records (via `pipeline/aggregate.py`) and fills `template/dashboard_template.html` to produce `site/index.html`.
4. The workflow syntax-checks the page and deploys it to GitHub Pages.
5. If the data changed, it commits `data/latest_meta.json`. That file is a small refresh log, and the commits also keep the daily schedule from being auto-disabled.

If any step fails, nothing is deployed and the previous dashboard stays live.

## API key

The key is stored as a repository secret named `DATA_GOV_SG_API_KEY` (**Settings → Secrets and variables → Actions**). It is never committed to the repo.

## One-time setup

1. Go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
2. Add the `DATA_GOV_SG_API_KEY` secret (see above).
3. Go to **Actions → Refresh dashboard → Run workflow**. The first run takes longer (about 5–15 min) because it downloads the ~800k historical rows once.

## Run locally

```bash
pip install -r requirements.txt
export DATA_GOV_SG_API_KEY=...        # optional, but gives higher rate limits
python pipeline/fetch.py && python pipeline/harmonize.py && python pipeline/build.py
open site/index.html
```

You can also drop CSVs you've downloaded by hand into `data/raw/` as `<dataset_id>.csv`. `fetch.py` uses them until its next weekly re-pull.
