import os, re, requests, pandas as pd
from datetime import date, timedelta
from .common import read_lines, ts_utc, save_csv_dedup, backoff

BASE = "https://api.biorxiv.org/details/biorxiv"

# bioRxiv has no keyword-search endpoint. The only documented way to enumerate
# posts is /details/biorxiv/<start>/<end>/<cursor>, which returns a page of
# records (title + abstract + category) for a date interval. So we pull a
# recent window for the bioprocess-relevant categories and match keywords
# locally against title + abstract.
WINDOW_DAYS = 60
CATEGORIES = ["bioengineering", "cell biology", "synthetic biology", "molecular biology"]
MAX_PAGES = 40  # guard rail: the API pages 30 records at a time

HEADERS = {"User-Agent": f"biotech-intel {os.environ.get('NCBI_EMAIL', 'contact@example.com')}"}


def _get_page(interval, cursor, category):
    for attempt in range(3):
        try:
            r = requests.get(
                f"{BASE}/{interval}/{cursor}/json",
                params={"category": category},
                headers=HEADERS,
                timeout=60,
            )
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            backoff(attempt)


def _tokens(kw):
    """
    Whole-word match patterns for one config keyword.

    Single-character tokens are dropped: the "T" in "CAR T cell manufacturing
    process" matches almost any abstract and pulled in cardiomyocyte and
    hydrogel preprints during tuning.
    """
    return [re.compile(rf"\b{re.escape(tok)}\b") for tok in kw.lower().split() if len(tok) > 1]


def match_keyword(text, kws):
    """
    Return the first keyword whose words all appear in text, else "".

    Config keywords are multi-word bioprocess phrases ("HEK293 dissolved
    oxygen"). Requiring every word rather than the literal phrase is what
    makes them hit real preprints — the exact phrase almost never occurs.
    """
    t = (text or "").lower()
    for kw in kws:
        pats = _tokens(kw)
        if pats and all(p.search(t) for p in pats):
            return kw
    return ""


def _total(data):
    try:
        return int((data.get("messages") or [{}])[0].get("total") or 0)
    except (TypeError, ValueError):
        return 0


def run():
    kws = read_lines("config/keywords.txt") or ["AAV vector production"]
    end = date.today()
    interval = f"{(end - timedelta(days=WINDOW_DAYS)).isoformat()}/{end.isoformat()}"

    rows, seen = [], set()
    for category in CATEGORIES:
        cursor = 0
        for _ in range(MAX_PAGES):
            data = _get_page(interval, cursor, category)
            collection = data.get("collection", [])
            if not collection:
                break

            for it in collection:
                doi = it.get("doi")
                if doi in seen:
                    continue
                kw = match_keyword(f"{it.get('title', '')} {it.get('abstract', '')}", kws)
                if not kw:
                    continue
                seen.add(doi)
                rows.append({
                    "source": "bioRxiv",
                    "doi": doi,
                    "title": it.get("title"),
                    "authors": it.get("authors"),
                    "category": it.get("category"),
                    "date": it.get("date"),
                    "matched_keyword": kw,
                    "url": f"https://www.biorxiv.org/content/{doi}v{it.get('version')}",
                    "ingested_at": ts_utc(),
                })

            cursor += len(collection)
            if cursor >= _total(data):
                break

    df = pd.DataFrame(rows)
    if not df.empty:
        save_csv_dedup(df, "biorxiv_preprints", "doi")
    return df
