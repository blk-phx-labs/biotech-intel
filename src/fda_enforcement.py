import os, requests, pandas as pd
from .common import ts_utc, save_csv_dedup, backoff

URL = "https://api.fda.gov/drug/enforcement.json"

# openFDA exposes enforcement (recall) data for drug, device and food only —
# there is no biologics/CBER enforcement endpoint, so cell and gene therapy
# products themselves are not in here. What is in here is the stated reason
# for every CDER recall, which is the public record of GMP failure modes:
# sterility assurance, CGMP deviations, particulates, endotoxin, potency and
# stability. We filter toward sterile/parenteral manufacturing, which is the
# part of that record a cell and gene therapy process owner can learn from.
REASON_TERMS = [
    "sterility", "aseptic", "CGMP", "endotoxin", "mycoplasma", "particulate",
    "contamination", "cross-contamination", "microbial", "subpotent",
    "superpotent", "potency", "stability", "impurities", "degradation",
    "container closure", "media fill", "out of specification",
]
DOSAGE_TERMS = [
    "injection", "injectable", "suspension", "lyophilized", "vial",
    "infusion", "intravenous", "sterile", "ophthalmic",
]

# Reason text → failure mode tag, first match wins. Gives the knowledge base a
# categorical column instead of only free text.
FAILURE_MODES = [
    ("sterility_assurance", ("sterility", "aseptic", "non-sterile", "nonsterile")),
    ("microbial_contamination", ("microbial", "endotoxin", "mycoplasma", "bacter", "fungal", "mold", "burkholderia")),
    ("particulate", ("particulate", "particle", "foreign material", "glass")),
    ("cgmp_deviation", ("cgmp", "gmp", "good manufacturing")),
    ("potency", ("potency", "subpotent", "superpotent", "assay", "content uniformity")),
    ("stability_impurity", ("stability", "impurit", "degrad", "out of specification", "nitrosamine")),
    ("container_closure", ("container closure", "leak", "seal", "crack")),
    ("labeling", ("label", "mislabel", "mix-up", "mixup")),
]

LIMIT = 100


def _params(search):
    """openFDA params; an API key raises the rate limit but is never required."""
    params = {"search": search, "limit": LIMIT, "sort": "report_date:desc"}
    key = os.environ.get("OPENFDA_API_KEY")
    if key:
        params["api_key"] = key
    return params


def build_search():
    reason = " OR ".join(f'"{t}"' for t in REASON_TERMS)
    dosage = " OR ".join(f'"{t}"' for t in DOSAGE_TERMS)
    return f"reason_for_recall:({reason}) AND product_description:({dosage})"


def classify_failure(reason):
    r = (reason or "").lower()
    for mode, needles in FAILURE_MODES:
        if any(n in r for n in needles):
            return mode
    return "other"


def run():
    for attempt in range(3):
        try:
            r = requests.get(URL, params=_params(build_search()), timeout=60)
            if r.status_code == 404:  # openFDA returns 404 for "no matches found"
                return pd.DataFrame()
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
            backoff(attempt)

    rows = []
    for it in r.json().get("results", []):
        recall_number = it.get("recall_number", "")
        rows.append({
            "source": "FDA Enforcement",
            "recall_number": recall_number,
            "event_id": it.get("event_id"),
            "company": it.get("recalling_firm"),
            "product": it.get("product_description"),
            "classification": it.get("classification"),
            "status": it.get("status"),
            "reason": it.get("reason_for_recall"),
            "failure_mode": classify_failure(it.get("reason_for_recall")),
            "voluntary_mandated": it.get("voluntary_mandated"),
            "firm_notification": it.get("initial_firm_notification"),
            "distribution_pattern": it.get("distribution_pattern"),
            "product_quantity": it.get("product_quantity"),
            "city": it.get("city"),
            "state": it.get("state"),
            "country": it.get("country"),
            "recall_initiation_date": it.get("recall_initiation_date"),
            "center_classification_date": it.get("center_classification_date"),
            "report_date": it.get("report_date"),
            "termination_date": it.get("termination_date"),
            "event_type": "recall",
            "url": f'{URL}?search=recall_number:"{recall_number}"',
            "ingested_at": ts_utc(),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        save_csv_dedup(df, "fda_enforcement", "recall_number")
    return df
