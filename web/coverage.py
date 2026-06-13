"""The Coverage Map — two complementary "how much do we cover?" lenses.

1. **Catalogue coverage** — jurisdiction × obligation-category grid of how many
   forms we hold, split by filing type (downloadable PDF / online / reference).
   Answers "where is our form catalogue thick or thin?".

2. **Portfolio coverage** — entity × file-status grid across the book, plus the
   headline "% filed/confirmed". Answers "how much of the client book is actually
   filed vs outstanding?" — Sphere's "X% compliant" feel.

Pure data assembly over the [[obligations]] + forms catalogue; the web layer turns
these grids into Plotly heatmaps. No persistence, no model.
"""
from __future__ import annotations

_DONE = {"filed", "confirmed"}


def catalogue_matrix(store) -> dict:
    """Jurisdiction × category grid of form counts (+ filing-type breakdown).

    Returns ``{jurs, cats, count[[]], dl[[]], on[[]], ref[[]], totals}`` where the
    grids are indexed ``[jurisdiction][category]``."""
    forms = store.list_forms(limit=8000)
    jurs = sorted({f["jurisdiction_code"] for f in forms if f.get("jurisdiction_code")})
    cats = sorted({f.get("category") or "other" for f in forms})
    ij = {j: i for i, j in enumerate(jurs)}
    ic = {c: i for i, c in enumerate(cats)}

    def grid():
        return [[0] * len(cats) for _ in jurs]

    count, dl, on, ref = grid(), grid(), grid(), grid()
    n_dl = n_on = n_ref = 0
    for f in forms:
        j = ij.get(f.get("jurisdiction_code"))
        c = ic.get(f.get("category") or "other")
        if j is None or c is None:
            continue
        count[j][c] += 1
        ft = f.get("filing_type") or "downloadable"
        if ft == "online":
            on[j][c] += 1; n_on += 1
        elif ft == "reference":
            ref[j][c] += 1; n_ref += 1
        else:
            dl[j][c] += 1; n_dl += 1
    return {"jurs": jurs, "cats": cats, "count": count, "dl": dl, "on": on, "ref": ref,
            "totals": {"forms": len(forms), "jurisdictions": len(jurs),
                       "downloadable": n_dl, "online": n_on, "reference": n_ref}}


def portfolio_matrix(store, statuses: list[str]) -> dict:
    """Entity × status grid over every obligation, plus headline coverage.

    ``statuses`` fixes the column order (the app's canonical OB_STATUS). Returns
    ``{rows:[{id,name,counts{status:n},total,filed,pct}], statuses, total, filed,
    pct, by_status}``."""
    ents = store.list_entities(limit=5000)
    obs = store.list_obligations(limit=20000)
    by_ent: dict = {}
    for o in obs:
        by_ent.setdefault(o.get("entity_id"), []).append(o)
    by_status = {s: 0 for s in statuses}
    rows = []
    for e in ents:
        eo = by_ent.get(e["id"], [])
        counts = {s: 0 for s in statuses}
        for o in eo:
            s = o.get("status") or "not_started"
            counts[s] = counts.get(s, 0) + 1
            by_status[s] = by_status.get(s, 0) + 1
        total = len(eo)
        filed = counts.get("filed", 0) + counts.get("confirmed", 0)
        rows.append({"id": e["id"], "name": e["name"], "counts": counts,
                     "total": total, "filed": filed,
                     "pct": round(100 * filed / total) if total else 0})
    rows.sort(key=lambda r: (-r["total"], r["name"]))
    total = len(obs)
    filed = sum(r["filed"] for r in rows)
    return {"rows": rows, "statuses": statuses, "total": total, "filed": filed,
            "pct": round(100 * filed / total) if total else 0, "by_status": by_status}
