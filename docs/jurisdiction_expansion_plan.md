# FastFund — Jurisdiction Expansion Plan

Extend coverage from the 6 MVP domiciles to **all jurisdictions FastFund
operates in** (sourced from fastfund.org/offices — ~24 distinct jurisdictions),
reusing the existing forms-first pattern. No new architecture — each
jurisdiction is just config + a scrape.

## ✅ Status — COMPLETE (all 24 + 2 implemented, 2026-06)

`config/tax_forms.yaml` now carries **26 jurisdiction blocks** covering every FastFund
office jurisdiction. All authority forms pages were researched and the URLs
verified before adding. Scraping yield (downloaded PDFs vs portal/metadata):

| Yield class | Jurisdictions | Notes |
|-------------|---------------|-------|
| **High downloadable PDF yield** | US/IRS (8 key forms), Hong Kong (BIR51/52/54 + notes), New Zealand (IR4 + guides), Luxembourg (126), Guernsey (71), Bermuda (ES declarations), Poland (CIT-8), Cyprus (TD4) | Real fillable/return PDFs harvested directly |
| **Metadata + portal link (online filing)** | Jersey, Ireland, Cayman, BVI, Isle of Man, Mauritius, Netherlands, UK, Germany, Switzerland, Singapore, UAE, Bahamas, Brazil, South Africa, Delaware/Wyoming/South Dakota | Returns filed via e-portal — we store the portal URL + full metadata |
| **Gated (metadata only)** | Malta (anti-bot), Austria K1 (interactive AcroForm loader) | Recorded with authority URL; PDF not directly fetchable |

Two infra notes from the build:
- **TLS:** `mof.gov.cy` (Cyprus) serves an incomplete cert chain — added to
  `ingest.fetch.INSECURE_HOSTS` so its valid-but-unchained PDFs download.
- **Discover-all** (`forms_index`) is enabled only for clean, bounded, static PDF
  indexes (GG, LU, PL, HK, NZ, BM); JS/portal sites are curated-only to avoid
  crawling e-filing SPAs or the IRS's 3,000-PDF catalogue.

---

## Original plan (for reference)

## The repeatable pattern (per jurisdiction)

For each new jurisdiction, add one block to **`config/tax_forms.yaml`**:

```yaml
XX:
  name: <Jurisdiction>
  authority: <Revenue authority>
  forms_index: <authority forms/downloads page>
  index_match: ".pdf"            # or a handler pattern (e.g. CHttpHandler, /media/)
  index_browser: false           # true if UA/JS/Cloudflare-gated (run headed)
  index_category: corporate_tax  # default category for discovered forms
  index_filing_type: downloadable # downloadable | online | reference
  forms:
    - form_key: ...              # curated key forms with who_files/deadline/legislation_ref
```

Then: `python3.12 -m ingest.cli --forms --jurisdiction XX`, review the audit
(downloadable vs online vs reference), and add the legislation provenance links
to `config/tax_sources.yaml` if law-Q&A is wanted there. The scraper, taxonomy,
filing-type labels, agents and UI all work unchanged.

## Coverage already live (MVP)

Jersey · Guernsey · Luxembourg · Ireland · Cayman · BVI.

## Expansion targets (FastFund office jurisdictions)

Tiered by fund-services relevance and scraping effort. **Filing reality** is the
key planning input — many authorities are online-only (store metadata + portal
link), some publish real downloadable forms.

### Tier 1 — fund/trust domiciles (highest value, similar to MVP)
| Jur | Authority | Forms source | Likely filing_type | Notes |
|-----|-----------|--------------|--------------------|-------|
| **Isle of Man** | Income Tax Division (gov.im) | gov.im/categories/tax-vat-and-your-money | downloadable + online | Online Tax Service; some PDF forms |
| **Mauritius** | Mauritius Revenue Authority (mra.mu) | mra.mu (forms) | downloadable + online | e-filing portal + PDF forms |
| **Bermuda** | Registrar of Companies / Govt | gov.bm | online + reference | No corp income tax; ES filings online |
| **Malta** | Commissioner for Revenue (cfr.gov.mt) | cfr.gov.mt | downloadable + online | EU; bilingual |
| **Cyprus** | Tax Department (mof.gov.cy) | mof.gov.cy/tax | downloadable + online | EU |

### Tier 2 — corporate / multinational hubs
| Jur | Authority | Forms source | Likely filing_type | Notes |
|-----|-----------|--------------|--------------------|-------|
| **Netherlands** | Belastingdienst | belastingdienst.nl | online | Mijn Belastingdienst Zakelijk; mostly online |
| **Switzerland** | ESTV/AFC + cantonal | estv.admin.ch + canton sites | downloadable + online | Multilingual (DE/FR/IT); cantonal PDFs |
| **United Kingdom** | HMRC | gov.uk/government/organisations/hm-revenue-customs | online + reference | CT600 filed online; PDFs are guidance |
| **Germany** | BZSt / ELSTER | elster.de, bzst.de | online | ELSTER portal; few downloadable |

### Tier 3 — Asia / Middle East / Americas
| Jur | Authority | Forms source | Likely filing_type | Notes |
|-----|-----------|--------------|--------------------|-------|
| **Singapore** | IRAS | iras.gov.sg | online + downloadable | myTax portal; JS-heavy (headed) |
| **Hong Kong** | IRD | ird.gov.hk | downloadable | Real PDF profits-tax return forms |
| **UAE** | Federal Tax Authority | tax.gov.ae | online | EmaraTax portal; new corporate tax |
| **USA (federal)** | IRS | irs.gov/forms-instructions | downloadable | Huge PDF form library (1120, 5472…) |
| **USA (states)** | DE Div. of Corporations; SD | corp.delaware.gov | downloadable + online | Entity/franchise filings |
| **South Africa** | SARS | sars.gov.za | online + downloadable | eFiling; some PDFs |

## Effort & gotchas
- **Online-only authorities** (NL, UK, UAE, DE) → store the form as `online` with
  the portal link; no PDF to download (the app already handles this).
- **Gated sites** (IRAS JS, any Cloudflare) → set `index_browser: true` and run
  the scrape headed (xvfb on the host) — see SKILLS.md.
- **Multilingual** (CH, DE, MEU) → expect language-variant duplicates; the
  latest-year dedup helps, and a language-suffix dedup can be added.
- **Direct endpoints** beat HTML pages (cf. gov.gg `CHttpHandler`); find the
  authority's file handler/CDN where possible.

## Suggested sequence
1. **Tier 1** first (Isle of Man, Mauritius) — closest to the MVP fund pattern.
2. Then **Tier 3 downloadables** (IRS, Hong Kong) — high PDF yield.
3. Then **online-only** hubs (NL, UK, UAE, DE) as metadata + portal links.
4. Backfill `legislation_ref` provenance per jurisdiction as needed.

Each tier is a few hours of config + a scrape + an audit review — no code changes.
