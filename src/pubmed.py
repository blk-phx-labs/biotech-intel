import os, requests, pandas as pd
from urllib.parse import quote_plus
from .common import read_lines, ts_utc, save_csv_dedup, backoff

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# NCBI E-utilities policy requires an identifying tool name + contact email on
# every request. Same pattern as SEC_USER_AGENT in sec_edgar.py: value comes
# from the environment, with a placeholder fallback so local runs still work.
TOOL = "biotech-intel"
EMAIL = os.environ.get("NCBI_EMAIL", "contact@example.com")
HEADERS = {"User-Agent": f"{TOOL} {EMAIL}"}


def _ident(params):
    """Attach the NCBI identifying params (and an API key, if one is set)."""
    params = {**params, "tool": TOOL, "email": EMAIL}
    key = os.environ.get("NCBI_API_KEY")
    if key:
        params["api_key"] = key
    return params


def _get(url, params):
    for attempt in range(3):
        try:
            r = requests.get(url, params=_ident(params), headers=HEADERS, timeout=60)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 2:
                raise
            backoff(attempt)

def build_query(kws):
    """
    Turn config/keywords.txt into a PubMed query.

    Each keyword is parenthesised and restricted to [tiab] (title/abstract).
    Without [tiab], PubMed's automatic term mapping expands a phrase like
    "critical process parameter" across MeSH and explodes a 90-record topic
    into 20,000 mostly irrelevant hits, which then dominate the retmax window.
    """
    kws = kws or ["AAV vector production"]
    return " OR ".join(f"({kw}[tiab])" for kw in kws)


def run():
    query = build_query(read_lines("config/keywords.txt"))

    ids = _get(ESEARCH, {"db": "pubmed", "term": query, "retmode": "json", "retmax": 200}).json()["esearchresult"]["idlist"]
    if not ids:
        return pd.DataFrame()

    summ = _get(ESUMMARY, {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}).json()["result"]
    rows = []
    for k, v in summ.items():
        if k == "uids":
            continue
        rows.append({
            "source": "PubMed",
            "pmid": v.get("uid"),
            "title": v.get("title"),
            "journal": v.get("fulljournalname"),
            "pubdate": v.get("pubdate"),
            "authors": "; ".join([a.get("name", "") for a in v.get("authors", [])]),
            "doi": next(((i.get("value") or i.get("identifier") or "") for i in v.get("articleids", []) if i.get("idtype") == "doi"), ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{v.get('uid')}/",
            "ingested_at": ts_utc(),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        save_csv_dedup(df, "pubmed_papers", "pmid")
    return df
