# ETC 2026 Match Explorer — live data setup

This version reads `data.json` instead of having the schedule baked into
`index.html`. A GitHub Action re-scrapes the official FIT site every hour
and updates `data.json` automatically — no manual re-uploads needed for
schedule/score changes going forward.

## One-time setup

1. Upload **all files in this folder**, preserving the folder structure —
   including the `.github/workflows/update-data.yml` file and the
   `scraper/` folder. (GitHub's drag-and-drop uploader preserves folders
   if you drag the whole unzipped folder in, or you can use `git push` —
   see below.)

2. **Enable Pages** (if not already): Settings → Pages → source =
   "Deploy from branch", branch `main`, folder `/ (root)`.

3. **Allow the Action to commit changes**: Settings → Actions → General →
   scroll to "Workflow permissions" → select **"Read and write
   permissions"** → Save. Without this the scraper will run but fail to
   push its updates.

4. **Trigger the first run manually** (don't wait for the hourly clock):
   go to the "Actions" tab → "Update fixtures data" workflow → "Run
   workflow" button. Check the logs — it should say how many matches it
   found for each division and finish with "Wrote NNN matches to
   data.json".

After that, it runs automatically at 5 minutes past every hour.

## If you'd rather use git directly

```bash
cd path/to/unzipped/folder
git init
git remote add origin https://github.com/<you>/<repo>.git
git add .
git commit -m "Live data version"
git branch -M main
git push -u origin main
```

## How it stays safe

The scraper refuses to overwrite `data.json` if it finds an unreasonably
low match count (under 300) — that usually means the site's page
structure changed slightly and the parser needs a small adjustment,
rather than the tournament actually losing matches. In that case the
last good `data.json` stays live and the Action log will show which
division(s) failed to parse, which is what to send back for a fix.

## What updates automatically vs. what doesn't

- **Fixture times/fields/rounds**: yes, hourly.
- **Results/scores**: yes, once the site publishes them (shows as a
  score badge on the match card instead of "vs").
- **Finals-stage team names** (e.g. "Winner QF1" → an actual team):
  yes, whenever the FIT site itself updates that.
- **True live, in-match score ticking**: no — this is hourly refresh,
  not a live scoreboard.
