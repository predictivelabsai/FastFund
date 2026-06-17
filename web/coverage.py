"""Service coverage matrix — SFO × service grid (held / recommended / gap).

The SFO-domain analogue of TaxHub's coverage matrix. For each family and each JTC
service it answers: do they already hold it (``held``), have we recommended it
(``rec``), or is it whitespace (``gap``)? This is the at-a-glance cross-sell map
for the sales team.
"""
from __future__ import annotations


def coverage_matrix(store, limit: int = 200) -> dict:
    """Return {services:[{key,name,category}], rows:[{sfo, cells:{key:state}}],
    totals:{held,rec,gap}} where state ∈ held|rec|gap."""
    services = store.list_services(limit=500)
    sfos = store.list_sfos(limit=limit)

    totals = {"held": 0, "rec": 0, "gap": 0}
    rows = []
    for s in sfos:
        held = set(s.get("current_services") or [])
        recadvised = {r["service_id"] for r in store.list_recommendations(sfo_id=s["id"])}
        cells = {}
        for svc in services:
            if svc["key"] in held:
                state = "held"
            elif svc["id"] in recadvised:
                state = "rec"
            else:
                state = "gap"
            cells[svc["key"]] = state
            totals[state] += 1
        rows.append({"sfo": s, "cells": cells})
    return {"services": services, "rows": rows, "totals": totals}


def service_clients(store, service_key: str, limit: int = 500) -> dict:
    """Who holds / has been recommended a given service — for the service page."""
    svc = store.get_service_by_key(service_key)
    if not svc:
        return {"holders": [], "recommended": []}
    holders, recommended = [], []
    for s in store.list_sfos(limit=limit):
        if service_key in (s.get("current_services") or []):
            holders.append(s)
        elif any(r["service_id"] == svc["id"] for r in store.list_recommendations(sfo_id=s["id"])):
            recommended.append(s)
    return {"holders": holders, "recommended": recommended}
