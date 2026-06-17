"""Graph data for the vis-network relationship view.

Two modes (analogue of TaxHub's ontology provenance/schema):

* **book** — the client book as a graph: families → the services they hold and
  the services recommended to them. Shows where cross-sell relationships connect
  families to whitespace.
* **schema** — the JTC service catalogue + the `CROSS_SELLS_TO` graph: how
  services bundle, independent of any client.
"""
from __future__ import annotations

# vis-network groups (JTC palette).
GROUPS = {
    "sfo": {"shape": "dot", "color": "#550055"},
    "service": {"shape": "box", "color": "#ba2a84"},
    "service_held": {"shape": "box", "color": "#6b1766"},
}


def build_book(store, limit: int = 40) -> dict:
    """Families (dots) linked to held services (solid) and recommended services
    (dashed). Service nodes are shared, so clusters reveal common whitespace."""
    nodes, edges, seen_svc = [], [], set()
    services = {s["id"]: s for s in store.list_services(limit=500)}

    def add_service(svc, held):
        nid = f"svc_{svc['id']}"
        if nid not in seen_svc:
            nodes.append({"id": nid, "label": svc["name"], "group": "service",
                          "serviceid": svc["id"]})
            seen_svc.add(nid)
        return nid

    for s in store.list_sfos(limit=limit):
        sid = f"sfo_{s['id']}"
        nodes.append({"id": sid, "label": s["name"].replace(" Family Office", ""),
                      "group": "sfo", "sfoid": s["id"],
                      "value": max(1, int((s.get("aum_usd") or 0) / 2e8))})
        held = set(s.get("current_services") or [])
        for key in held:
            svc = store.get_service_by_key(key)
            if svc:
                edges.append({"from": sid, "to": add_service(svc, True),
                              "color": {"color": "#6b1766"}, "title": "holds"})
        for r in store.list_recommendations(sfo_id=s["id"]):
            svc = services.get(r["service_id"])
            if svc and svc["key"] not in held:
                edges.append({"from": sid, "to": add_service(svc, False),
                              "dashes": True, "color": {"color": "#cdbcd0"},
                              "title": f"recommended ({r.get('kind')})"})
    return {"nodes": nodes, "edges": edges,
            "stats": f"{sum(1 for n in nodes if n['group']=='sfo')} families · "
                     f"{len(seen_svc)} services"}


def build_schema(store) -> dict:
    """Service nodes + CROSS_SELLS_TO edges — the cross-sell knowledge graph."""
    services = store.list_services(limit=500)
    nodes = [{"id": f"svc_{s['id']}", "label": s["name"], "group": "service",
              "serviceid": s["id"], "title": f"{s.get('category')} · {s.get('tier')}"}
             for s in services]
    edges = []
    for s in services:
        for partner in store.list_cross_sells(s["key"]):
            edges.append({"from": f"svc_{s['id']}", "to": f"svc_{partner['id']}",
                          "value": partner.get("weight", 0.5),
                          "label": f"{partner.get('weight',0):.0%}",
                          "color": {"color": "#cdbcd0"}})
    return {"nodes": nodes, "edges": edges,
            "stats": f"{len(nodes)} services · {len(edges)} cross-sell links"}
