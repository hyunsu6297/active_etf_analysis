# Active ETF Analysis

Static dashboard for comparing active ETF holdings against BM weights.

## Site

After GitHub Pages is enabled with the GitHub Actions source, the dashboard is served at:

https://hyunsu6297.github.io/active_etf_analysis/

## Daily update

The workflow in `.github/workflows/update-dashboard.yml` runs every day at 08:00 Korea time.
It downloads the target day's ETF holding files, stores raw/parquet data under `data/`,
rebuilds `etf_active_weight_dashboard.html`, copies it to `index.html`, commits the result,
and deploys the static site to GitHub Pages.

You can also run it manually from the Actions tab with an optional `YYYY-MM-DD` target date.
