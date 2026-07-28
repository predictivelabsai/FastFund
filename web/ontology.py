"""Ontology graph for the Help section.

Two NetworkX-built views, serialised to vis-network ``{nodes, edges}``:

* **schema**   — the meta-graph: how the graph DB is organised (node labels +
                 relationship types, with live counts). Answers "what is the model".
* **instance** — the provenance graph: Jurisdiction → Form → the Legislation the
                 form implements. Forms that implement the same law share a
                 Legislation node, so the cross-links (one law → many forms across
                 jurisdictions) become visible — the "trace a form back to the law"
                 story. Built from each form's ``legislation_ref`` (no schema
                 migration needed); degree is computed by NetworkX for node sizing.
"""
from __future__ import annotations

from urllib.parse import urlparse

import networkx as nx

# vis-network group keys → styled in the page CSS/options.
G_JUR, G_LEG, G_DOC = "jurisdiction", "legislation", "document"
_FORM_GROUP = {"downloadable": "form_downloadable", "online": "form_online",
               "reference": "form_reference"}

# Instance graph is capped so the force layout stays legible.
_MAX_FORMS = 320


def _leg_label(url: str) -> str:
    """A short, human label for a legislation URL (host + last path segment)."""
    try:
        u = urlparse(url)
        host = (u.netloc or "").replace("www.", "")
        tail = [p for p in (u.path or "").split("/") if p]
        last = tail[-1] if tail else ""
        last = last.rsplit(".", 1)[0][:40]  # drop extension, clip
        return f"{host}/{last}" if last else host or url[:48]
    except Exception:  # noqa: BLE001
        return (url or "")[:48]


def build_schema(store) -> dict:
    """Meta-graph: labels (with counts) + relationship types."""
    jurs = store.list_jurisdictions_with_counts()
    n_jur = len(jurs)
    n_doc = sum(int(j.get("docs") or 0) for j in jurs)
    n_form = len(store.list_forms(limit=5000))
    try:
        n_change = len(store.recent_changes(2000))
    except Exception:  # noqa: BLE001
        n_change = 0
    try:
        n_chunk = store.count_chunks()
    except Exception:  # noqa: BLE001
        n_chunk = 0

    # (id, label, group, count)
    labels = [
        ("Jurisdiction", G_JUR, n_jur),
        ("Form", "form_downloadable", n_form),
        ("Legislation", G_LEG, None),
        ("Document", G_DOC, n_doc),
        ("Version", G_DOC, None),
        ("Change", G_DOC, n_change),
        ("Chunk", G_DOC, n_chunk),
    ]
    rels = [
        ("Jurisdiction", "Form", "HAS_FORM"),
        ("Form", "Legislation", "IMPLEMENTS"),
        ("Jurisdiction", "Document", "HAS_DOCUMENT"),
        ("Document", "Version", "HAS_VERSION"),
        ("Version", "Change", "HAS_CHANGE"),
        ("Version", "Chunk", "HAS_CHUNK"),
    ]
    nodes = []
    for lab, grp, cnt in labels:
        txt = f"{lab}\n({cnt})" if cnt is not None else lab
        nodes.append({"id": lab, "label": txt, "group": grp,
                      "value": 20 + (cnt or 0) ** 0.5})
    edges = [{"from": a, "to": b, "label": r, "arrows": "to"} for a, b, r in rels]
    return {"nodes": nodes, "edges": edges,
            "stats": f"{n_jur} jurisdictions · {n_form} forms · {n_doc} documents"}


def build_instance(store, jurisdiction: str | None = None) -> dict:
    """Provenance graph: Jurisdiction → Form → Legislation (via legislation_ref)."""
    forms = store.list_forms(jurisdiction_code=jurisdiction or None, limit=5000)
    truncated = len(forms) > _MAX_FORMS
    forms = forms[:_MAX_FORMS]

    g = nx.DiGraph()
    meta: dict[str, dict] = {}  # node id -> extra payload for serialisation

    for f in forms:
        jc = f["jurisdiction_code"]
        jid = f"J:{jc}"
        if jid not in meta:
            g.add_node(jid)
            meta[jid] = {"label": jc, "group": G_JUR, "jur": jc}
        fid = f"F:{f['id']}"
        g.add_node(fid)
        g.add_edge(jid, fid, label="HAS_FORM")
        meta[fid] = {"label": (f.get("title") or "form")[:48],
                     "group": _FORM_GROUP.get(f.get("filing_type") or "downloadable",
                                              "form_downloadable"),
                     "formid": f["id"], "full": f.get("title") or ""}
        leg = (f.get("legislation_ref") or "").strip()
        if leg:
            lid = f"L:{leg}"
            if lid not in meta:
                g.add_node(lid)
                meta[lid] = {"label": _leg_label(leg), "group": G_LEG, "url": leg}
            g.add_edge(fid, lid, label="IMPLEMENTS")

    deg = dict(g.degree())
    nodes = []
    for nid, m in meta.items():
        d = deg.get(nid, 1)
        nodes.append({"id": nid, "label": m["label"], "group": m["group"],
                      "value": 8 + d * 6,
                      "title": m.get("full") or m.get("url") or m["label"],
                      **({"formid": m["formid"]} if "formid" in m else {}),
                      **({"url": m["url"]} if "url" in m else {})})
    edges = [{"from": u, "to": v, "label": d.get("label", ""), "arrows": "to"}
             for u, v, d in g.edges(data=True)]

    nleg = sum(1 for m in meta.values() if m["group"] == G_LEG)
    nfrm = sum(1 for m in meta.values() if m["group"].startswith("form"))
    # most-implemented law (highest in-degree legislation node)
    top = max(((nid, g.in_degree(nid)) for nid, m in meta.items()
               if m["group"] == G_LEG), key=lambda x: x[1], default=(None, 0))
    top_lbl = meta[top[0]]["label"] if top[0] else "—"
    stats = (f"{nfrm} forms · {nleg} laws linked · most-implemented: "
             f"{top_lbl} ({top[1]} forms)")
    if truncated:
        stats += f" · showing first {_MAX_FORMS} (filter by jurisdiction)"
    return {"nodes": nodes, "edges": edges, "stats": stats}
