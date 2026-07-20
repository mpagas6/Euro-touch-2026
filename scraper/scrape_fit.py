#!/usr/bin/env python3
"""
Scrapes the official FIT European Championships 2026 site for match
fixtures/results across all divisions, and writes a single data.json
that the front-end reads. Designed to run hourly via GitHub Actions.

Safety principle: if a scrape looks broken (way fewer matches than
expected, or a network/parse error), we do NOT overwrite the existing
data.json. It's better to show slightly-stale-but-correct data than
to publish garbage.
"""
import json
import re
import sys
import time
import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.internationaltouch.org/events/euros/2026"

DIVISIONS = {
    "mo":    {"slug": "mens-open",    "label": "Men's Open"},
    "wo":    {"slug": "womens-open",  "label": "Women's Open"},
    "xo":    {"slug": "mixed-open",   "label": "Mixed Open"},
    "w27":   {"slug": "womens-27",    "label": "Women's 27"},
    "m3035": {"slug": "mens-3035",    "label": "Men's 30/35"},
    "x30":   {"slug": "mixed-30",     "label": "Mixed 30"},
    "w35":   {"slug": "womens-35",    "label": "Women's 35"},
    "m40":   {"slug": "mens-40",      "label": "Men's 40"},
    "w40":   {"slug": "womens-40",    "label": "Women's 40"},
    "m45":   {"slug": "mens-45",      "label": "Men's 45"},
    "m5055": {"slug": "mens-5055",    "label": "Men's 50/55"},
}

# Known team slug -> nation code, for filter purposes. Extend as needed;
# unknown slugs fall back to blank code (still displayed, just unfiltered).
TEAM_CODES = {
    "england": "ENG", "wales": "WAL", "ireland": "IRL", "scotland": "SCO",
    "france": "FRA", "spain": "ESP", "italy": "ITA", "portugal": "PRT",
    "belgium": "BEL", "switzerland": "CHE", "germany": "DEU",
    "netherlands": "NLD", "cayman-islands": "CYM", "jersey": "JEY",
    "chile": "CHL",
    "england-m35": "ENG", "wales-m35": "WAL",
    "england-m55": "ENG", "wales-m55": "WAL", "portugal-m55": "PRT",
    "england-w45": "ENG",
    "euroselect-wo": "EUR", "euroselect-senior-women": "EUR",
    "eurostars": "EUR",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ETC2026FixtureBot/1.0; "
                  "+https://github.com/) personal-use fixture sync"
}

DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
FIELD_RE = re.compile(r"Field\s+(\d+)", re.IGNORECASE)
SCORE_RE = re.compile(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b")

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def fetch(url, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def parse_date_heading(text):
    m = DATE_RE.search(text)
    if not m:
        return None
    day, mon, year = m.groups()
    month = MONTHS.get(mon[:3].title())
    if not month:
        return None
    try:
        return datetime.date(int(year), month, int(day)).isoformat()
    except ValueError:
        return None


def cell_team(cell):
    """Extract (display_name, slug) from a team table cell.
    Some pages render the team name with its code appended inline
    (e.g. "Belgium BEL") — strip that trailing code if present so the
    display name is just "Belgium"."""
    a = cell.find("a")
    if a and a.get("href"):
        name = a.get_text(" ", strip=True)
        slug = a["href"].rstrip("/").split("/")[-1]
        code = TEAM_CODES.get(slug, "")
        if code and re.search(rf"\s{code}$", name, flags=re.IGNORECASE):
            name = re.sub(rf"\s*{code}$", "", name, flags=re.IGNORECASE).strip()
        return name, slug
    text = cell.get_text(" ", strip=True)
    return text, ""


def extract_time_24h(text):
    matches = TIME_RE.findall(text)
    if not matches:
        return None
    h, m = matches[-1]  # the 24h version is always the last occurrence
    return f"{int(h):02d}:{m}"


def parse_division(code, meta):
    url = f"{BASE}/{meta['slug']}/"
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    matches = []
    current_date = None
    current_round = None

    # Walk the document in order so headings correctly attribute to the
    # tables/rows that follow them.
    body = soup.body or soup
    for el in body.find_all(["h2", "h3", "h4", "h5", "table"]):
        if el.name in ("h2", "h3", "h4", "h5"):
            d = parse_date_heading(el.get_text(" ", strip=True))
            if d:
                current_date = d
            continue

        # el.name == "table"
        for tr in el.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 8:
                continue

            # Round cell is only present (rowspan) on the first row of a
            # group; carry the previous value forward otherwise.
            offset = 0
            first_cell_text = cells[0].get_text(" ", strip=True)
            looks_like_round = bool(
                re.search(r"round|final|playoff|medal|placing", first_cell_text, re.I)
            ) and not TIME_RE.search(first_cell_text)
            if len(cells) == 9 or (len(cells) >= 9 and looks_like_round):
                current_round = first_cell_text
                offset = 1

            try:
                time_cell = cells[offset + 0]
                field_cell = cells[offset + 1]
                team_a_cell = cells[offset + 2]
                team_b_cell = cells[offset + 6]
                link_cell = cells[offset + 7]
            except IndexError:
                continue

            match_link = link_cell.find("a", href=re.compile(r"/match:\d+/?$"))
            if not match_link:
                # sometimes the stats link sits in a different cell; scan row
                match_link = tr.find("a", href=re.compile(r"/match:\d+/?$"))
            if not match_link:
                continue
            mid_match = re.search(r"/match:(\d+)/?", match_link["href"])
            if not mid_match:
                continue
            match_id = int(mid_match.group(1))

            row_text = time_cell.get_text(" ", strip=True)
            t = extract_time_24h(row_text)
            field_m = FIELD_RE.search(field_cell.get_text(" ", strip=True))
            field = f"Field {field_m.group(1)}" if field_m else field_cell.get_text(" ", strip=True)

            name_a, slug_a = cell_team(team_a_cell)
            name_b, slug_b = cell_team(team_b_cell)
            code_a = TEAM_CODES.get(slug_a, "")
            code_b = TEAM_CODES.get(slug_b, "")

            has_video = bool(tr.find("a", href=re.compile(r"/video/?$")))

            score_m = SCORE_RE.search(tr.get_text(" ", strip=True))
            score = None
            if score_m and (slug_a or slug_b):
                score = f"{score_m.group(1)}-{score_m.group(2)}"

            is_final = (not slug_a) or (not slug_b)

            if not (current_date and t and name_a and name_b):
                continue

            matches.append({
                "cat": code,
                "date": current_date,
                "time": t,
                "field": field,
                "teamA": name_a, "codeA": code_a,
                "teamB": name_b, "codeB": code_b,
                "round": current_round or "",
                "id": match_id,
                "isFinal": 1 if is_final else 0,
                "video": has_video,
                "score": score,
            })

    # de-dupe by match id (in case of any structural overlap)
    seen = {}
    for m in matches:
        seen[m["id"]] = m
    return list(seen.values())


def main():
    out_path = Path(__file__).resolve().parent.parent / "data.json"
    existing = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text()).get("matches", [])
        except Exception:  # noqa: BLE001
            existing = []

    all_matches = []
    errors = []
    for code, meta in DIVISIONS.items():
        try:
            division_matches = parse_division(code, meta)
            print(f"{meta['label']}: {len(division_matches)} matches", file=sys.stderr)
            all_matches.extend(division_matches)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR scraping {meta['label']}: {e}", file=sys.stderr)
            errors.append(meta["label"])

    # Safety check: only publish if the result looks sane. 358 matches were
    # present at build time; allow drift but reject obvious breakage.
    MIN_EXPECTED = 300
    if len(all_matches) < MIN_EXPECTED:
        print(
            f"REFUSING TO PUBLISH: only found {len(all_matches)} matches "
            f"(expected >= {MIN_EXPECTED}). Keeping existing data.json. "
            f"Divisions with errors: {errors}",
            file=sys.stderr,
        )
        if existing:
            sys.exit(0)  # exit cleanly, workflow will see no file changes
        else:
            sys.exit(1)  # no existing data to fall back on either — fail loudly

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "match_count": len(all_matches),
        "divisions": {k: v["label"] for k, v in DIVISIONS.items()},
        "matches": all_matches,
    }
    out_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    print(f"Wrote {len(all_matches)} matches to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
