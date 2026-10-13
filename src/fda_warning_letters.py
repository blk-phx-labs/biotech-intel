import io, os, time, requests, pandas as pd
from .common import ts_utc, save_csv_dedup, backoff, stable_hash

PAGE = ("https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations"
        "/compliance-actions-and-activities/warning-letters")
URL = f"{PAGE}/datatables-data"

# FDA publishes no warning-letter API. The public warning letters table does
# expose an official structured export (the "Export" control on the page
# above), which returns the table as XLSX and honours the same
# search_api_fulltext filter the page itself uses. That is what we read — no
# HTML scraping. www.fda.gov/robots.txt does not disallow this path but sets
# "Crawl-Delay: 30" for anonymous clients, which CRAWL_DELAY respects.
CRAWL_DELAY = 30
QUERIES = ["CGMP", "Human Cells, Tissues", "sterility", "biologics"]

# The table covers every FDA centre, so the bulk of it is tobacco, food and
# import letters. Keep biologics / sterile-drug manufacturing, drop the rest.
INCLUDE = (
    "cgmp", "biologic", "cells, tissues", "hct/p", "sterility", "sterile",
    "aseptic", "insanitary conditions", "gene therapy", "cell therapy",
    "blood", "plasma", "active pharmaceutical ingredient",
    "finished pharmaceutical", "compounding", "deviations",
)
EXCLUDE = (
    "tobacco", "food", "seafood", "juice", "dietary supplement", "animal",
    "veterinary", "produce safety", "fsvp", "infant formula", "medicated feed",
    "e-cigarette", "vape", "acidified", "low-acid", "medical devices", "qsr",
)

FOCUS = [
    ("cell_gene_therapy", ("cells, tissues", "hct/p", "biologic", "gene therapy", "cell therapy")),
    ("sterile_manufacturing", ("sterility", "sterile", "aseptic", "compounding", "insanitary")),
]

HEADERS = {"User-Agent": f"biotech-intel {os.environ.get('FDA_CONTACT_EMAIL', 'contact@example.com')}"}

COLUMNS = ["Posted Date", "Letter Issue Date", "Company Name", "Issuing Office",
           "Subject", "Response Letter", "Closeout Letter"]


def _fetch(query):
    """Return the export for one full-text query as a DataFrame (empty if none)."""
    for attempt in range(3):
        try:
            r = requests.get(
                URL,
                params={"_format": "xlsx", "search_api_fulltext": query},
                headers=HEADERS,
                timeout=120,
            )
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
            backoff(attempt)

    df = pd.read_excel(io.BytesIO(r.content))
    # A query with no hits exports an empty sheet with no header row.
    if not set(COLUMNS).issubset(df.columns):
        return pd.DataFrame(columns=COLUMNS)
    return df


def _blob(row):
    return " | ".join(str(row.get(c) or "") for c in
                      ("Subject", "Issuing Office", "Company Name")).lower()


def is_relevant(row):
    b = _blob(row)
    return any(i in b for i in INCLUDE) and not any(e in b for e in EXCLUDE)


def classify_focus(row):
    b = _blob(row)
    for focus, needles in FOCUS:
        if any(n in b for n in needles):
            return focus
    return "gmp_general"


def run():
    rows, seen = [], set()
    for i, query in enumerate(QUERIES):
        if i:
            time.sleep(CRAWL_DELAY)
        df = _fetch(query)
        for _, it in df.iterrows():
            row = it.to_dict()
            company = str(row.get("Company Name") or "")
            issued = str(row.get("Letter Issue Date") or "")
            subject = str(row.get("Subject") or "")
            letter_id = stable_hash(f"{company}|{issued}|{subject}")
            if letter_id in seen or not is_relevant(row):
                continue
            seen.add(letter_id)
            rows.append({
                "source": "FDA Warning Letters",
                "letter_id": letter_id,
                "company": company,
                "issuing_office": str(row.get("Issuing Office") or ""),
                "subject": subject,
                "focus": classify_focus(row),
                "letter_issue_date": issued,
                "posted_date": str(row.get("Posted Date") or ""),
                "response_letter": str(row.get("Response Letter") or ""),
                "closeout_letter": str(row.get("Closeout Letter") or ""),
                "matched_query": query,
                "event_type": "warning_letter",
                "url": f"{PAGE}?search_api_fulltext={requests.utils.quote(company)}",
                "ingested_at": ts_utc(),
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        save_csv_dedup(df, "fda_warning_letters", "letter_id")
    return df
