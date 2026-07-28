"""Synthetic data generator + seeder for FastFund.

Privacy-compliant demo data: a JTC service catalogue loaded from
``data/services.yaml``, plus N fictional single family offices with realistic,
correlated attributes (higher AUM → more services and more complex needs),
seeded conversations, and a recommendation funnel produced by the real engine.

Usage:
    python -m data.synth --services-only          # seed the catalogue + graph
    python -m data.synth --count 80               # seed catalogue + 80 SFOs + funnel
    python -m data.synth --count 80 --seed 7      # reproducible

No real client data is ever used. ``Faker`` drives names; allocations and needs
are sampled from public-benchmark-shaped distributions.
"""
from __future__ import annotations

import argparse
import os
import random

import yaml

import sfostore as store

_HERE = os.path.dirname(__file__)

ALL_SERVICES = ["trusts", "tax_reporting", "fund_admin", "luxury_assets",
                "real_estate_admin", "edge", "governance", "nextgen_education",
                "banking_treasury", "compliance", "private_office"]
DOMICILES = ["JE", "GG", "LU", "IE", "KY", "VG", "GB", "CH", "SG", "US", "AE"]
PAIN_POINTS = [
    "succession planning conflict between generations",
    "no formal governance framework",
    "consolidated reporting complexity across entities",
    "regulatory and AEOI (FATCA/CRS) reporting burden",
    "fragmented banking and cash-management relationships",
    "luxury asset (yacht/art) administration overhead",
    "limited visibility into private-equity holdings",
    "cross-jurisdiction tax compliance complexity",
    "preparing the next generation for stewardship",
]
STAGES = ["lead", "onboarding", "client"]


def load_catalogue() -> dict:
    with open(os.path.join(_HERE, "services.yaml")) as f:
        return yaml.safe_load(f)


def seed_services() -> int:
    cat = load_catalogue()
    for s in cat.get("services", []):
        store.upsert_service(s)
    for e in cat.get("cross_sells", []):
        store.add_cross_sell_edge(e["from"], e["to"], float(e.get("weight", 1.0)))
    return len(cat.get("services", []))


def _asset_mix(rng: random.Random) -> dict:
    """A correlated, ~100%-summing allocation, PE/RE/public-equity heavy."""
    pe = rng.randint(10, 35)
    re = rng.randint(5, 25)
    pub = rng.randint(10, 35)
    luxury = rng.choice([0, 0, 2, 5, 8, 12])
    cash = rng.randint(3, 12)
    alt = max(0, 100 - pe - re - pub - luxury - cash)
    return {"private_equity": pe, "real_estate": re, "public_equity": pub,
            "luxury": luxury, "cash": cash, "alternatives": alt}


def _services_for(aum: float, rng: random.Random) -> list[str]:
    """Bigger families hold more — but never the full stack (that's the upside)."""
    base = ["trusts"] if rng.random() < 0.7 else []
    n = 1 if aum < 250e6 else (2 if aum < 1e9 else 3)
    pool = [s for s in ALL_SERVICES if s not in base
            and s not in ("private_office", "nextgen_education")]
    base += rng.sample(pool, k=min(n, len(pool)))
    return sorted(set(base))


def make_sfo(i: int, rng: random.Random, fake) -> dict:
    family = fake.last_name()
    aum = rng.choice([100e6, 180e6, 250e6, 400e6, 600e6, 900e6, 1.5e9, 2.5e9, 4e9])
    aum = round(aum * rng.uniform(0.85, 1.3), -6)
    gens = rng.choice([1, 2, 2, 3, 3, 4])
    dom = rng.choice(DOMICILES)
    jurs = sorted(set([dom] + rng.sample(DOMICILES, k=rng.randint(0, 2))))
    return {
        "client_ref": f"SFO-{i:04d}",
        "name": f"{family} Family Office",
        "family_name": family,
        "aum_usd": aum,
        "family_size": rng.randint(3, 18),
        "generations": gens,
        "domicile": dom,
        "jurisdictions": jurs,
        "current_services": _services_for(aum, rng),
        "asset_mix": _asset_mix(rng),
        "pain_points": rng.sample(PAIN_POINTS, k=rng.randint(1, 3)),
        "stage": rng.choices(STAGES, weights=[3, 2, 5])[0],
        "contact_name": fake.name(),
        "contact_email": fake.email(),
    }


def seed_sfos(count: int, rng: random.Random) -> list[int]:
    from faker import Faker
    fake = Faker()
    Faker.seed(rng.randint(0, 10**6))
    ids = []
    for i in range(1, count + 1):
        ids.append(store.upsert_sfo(make_sfo(i, rng, fake)))
    return ids


def seed_funnel(sfo_ids: list[int], rng: random.Random) -> int:
    """Run the rule engine for each SFO and advance some recs along the funnel."""
    from engine import crosssell
    total = 0
    for sid in sfo_ids:
        recs = crosssell.recommend(sid, persist=True, use_ai=False)
        total += len(recs)
        for r in recs:
            # Simulate a realistic funnel: most stay suggested, some progress.
            roll = rng.random()
            status = ("booked" if roll < 0.08 else "accepted" if roll < 0.20
                      else "presented" if roll < 0.45 else "declined" if roll < 0.52
                      else "suggested")
            if status != "suggested":
                existing = store.list_recommendations(sfo_id=sid)
                match = next((x for x in existing if x["service_id"] == r["service_id"]), None)
                if match:
                    store.set_recommendation_status(match["id"], status)
    return total


ROLES = ["principal", "spouse", "next_gen", "next_gen", "advisor"]
DOC_TEMPLATES = [
    ("Portfolio summary {yr}.pdf", "portfolio"),
    ("Trust deed - {fam} Family Trust.pdf", "trust_deed"),
    ("Luxury asset inventory.pdf", "asset_inventory"),
    ("Consolidated report Q{q}.pdf", "report"),
]
# A few realistic intake→upsell scripts the advisor would have.
CONVO_SCRIPTS = [
    [("user", "Tell me about our family's current governance setup."),
     ("assistant", "From your profile you have trust structures in place but no formal "
      "governance framework, and you're now spanning {gens} generations. A family "
      "constitution and council would give the rising generation a clear decision "
      "framework. Shall I outline how we'd approach it?"),
     ("user", "Yes, and what else should we be thinking about?"),
     ("assistant", "Alongside governance, your private-equity exposure is large enough "
      "that dedicated fund administration would reduce operational load, and JTC Edge "
      "would give you consolidated reporting across everything. I can prepare a short "
      "proposal for each.")],
    [("user", "We're acquiring a yacht and some art — can you help administer them?"),
     ("assistant", "Absolutely. Our luxury asset administration handles ownership "
      "structuring, registration and insurance coordination for yachts, aircraft and "
      "art. Given your existing trusts we can hold these efficiently and report them "
      "through Edge. Would you like me to book a consultation?")],
    [("user", "Reporting across our entities has become painful."),
     ("assistant", "That's a common pain point as portfolios diversify. JTC Edge "
      "consolidates multi-entity, multi-currency positions into one real-time view. "
      "I'd pair it with our tax reporting service so compliance flows from the same "
      "data. Shall I draft a proposal?")],
]


def seed_members(sfo_ids: list[int], rng: random.Random, fake) -> int:
    n = 0
    for sid in sfo_ids:
        sfo = store.get_sfo(sid)
        fam = sfo.get("family_name") or "Family"
        size = min(sfo.get("family_size") or 4, 6)
        for i in range(max(2, size // 2)):
            role = ROLES[min(i, len(ROLES) - 1)]
            gen = 1 if role in ("principal", "spouse") else rng.choice([2, 3])
            age = rng.randint(55, 78) if gen == 1 else rng.randint(18, 45)
            store.upsert_family_member({
                "sfo_id": sid, "name": f"{fake.first_name()} {fam}", "role": role,
                "generation": gen, "age": age,
                "notes": "Active in the business" if role == "principal" else ""})
            n += 1
    return n


_HOLDING_NAMES = {
    "private_equity": ["{fam} Private Equity Fund {rn}", "Project {city} (direct deal)",
                       "Co-invest — {sector}", "Growth buyout fund {rn}"],
    "real_estate": ["{city} commercial property", "Residential portfolio",
                    "Logistics & industrial REIT", "Prime office asset {city}"],
    "public_equity": ["Global equity managed account", "Developed-markets index portfolio",
                      "Thematic equity sleeve"],
    "luxury": ["Superyacht 'M/Y {fam}'", "Fine art collection", "Private aircraft",
               "Classic car collection"],
    "cash": ["Treasury / money-market", "Multi-currency deposits"],
    "alternatives": ["Hedge fund allocation", "Private credit fund", "Infrastructure fund {rn}"],
}
_PERF = {"private_equity": (8, 25), "real_estate": (4, 12), "public_equity": (-5, 20),
         "luxury": (-2, 6), "cash": (1, 4), "alternatives": (3, 15)}
_SECTORS = ["technology", "healthcare", "industrials", "consumer", "energy transition"]
_CITIES = ["London", "Zurich", "Singapore", "New York", "Dubai", "Geneva", "Munich"]
_ROMAN = ["I", "II", "III", "IV", "V"]


def seed_portfolio(sfo_ids: list[int], rng: random.Random) -> tuple[int, int]:
    """Holdings (correlated with asset_mix × AUM, with performance) + cash-flow txns."""
    from datetime import date, timedelta
    nh = nt = 0
    for sid in sfo_ids:
        sfo = store.get_sfo(sid)
        aum = float(sfo.get("aum_usd") or 0)
        mix = sfo.get("asset_mix") or {}
        for ac, pct in mix.items():
            if not pct or ac not in _HOLDING_NAMES:
                continue
            class_value = aum * pct / 100.0
            k = 1 if pct < 12 else rng.randint(2, 3)
            names = rng.sample(_HOLDING_NAMES[ac], k=min(k, len(_HOLDING_NAMES[ac])))
            shares = [rng.random() for _ in names]
            tot = sum(shares) or 1
            lo, hi = _PERF[ac]
            for nm, sh in zip(names, shares):
                name = nm.format(fam=sfo.get("family_name", "Family"),
                                 rn=rng.choice(_ROMAN), city=rng.choice(_CITIES),
                                 sector=rng.choice(_SECTORS))
                store.add_holding({
                    "sfo_id": sid, "name": name, "asset_class": ac,
                    "value_usd": round(class_value * sh / tot, -4),
                    "performance_pct": round(rng.uniform(lo, hi), 1)})
                nh += 1
        for _ in range(rng.randint(3, 7)):
            kind = rng.choice(["capital_call", "distribution", "buy", "sell", "fee"])
            mag = aum * rng.uniform(0.002, 0.03)
            amt = -mag if kind in ("capital_call", "buy", "fee") else mag
            d = (date.today() - timedelta(days=rng.randint(5, 360))).isoformat()
            store.add_transaction({
                "sfo_id": sid, "txn_date": d, "kind": kind, "amount_usd": round(amt, -3),
                "description": {"capital_call": "PE fund drawdown",
                                "distribution": "Fund realisation / distribution",
                                "buy": "Direct investment", "sell": "Asset disposal",
                                "fee": "Management & admin fees"}[kind]})
            nt += 1
    return nh, nt


def _doc_text(sfo: dict, dtype: str) -> str:
    fam = sfo.get("family_name", "Family")
    aum = (sfo.get("aum_usd") or 0) / 1e6
    mix = ", ".join(f"{k.replace('_',' ')} {v}%" for k, v in (sfo.get("asset_mix") or {}).items() if v)
    if dtype == "trust_deed":
        return (f"DEED OF TRUST\n\nThe {fam} Family Trust\n\nThis settlement is made between the "
                f"Settlor (the {fam} family) and the Trustee, JTC, governed by the laws of "
                f"{sfo.get('domicile','Jersey')}. The trust fund is held for the benefit of the "
                f"beneficiaries across {sfo.get('generations',2)} generations. Standard "
                f"administrative, investment and distribution powers apply.")
    if dtype == "asset_inventory":
        return (f"LUXURY ASSET INVENTORY — {fam} Family Office\n\nSchedule of passion and luxury "
                f"assets held: superyacht, fine art collection, private aircraft and classic "
                f"vehicles. Each asset requires ownership structuring, registration and insurance "
                f"coordination. Estimated luxury allocation per the latest portfolio summary.")
    # portfolio / report
    return (f"PORTFOLIO SUMMARY — {fam} Family Office\n\nAssets under management: "
            f"approximately ${aum:,.0f}M across {sfo.get('generations',2)} generations.\n"
            f"Asset allocation: {mix}.\nKey pain points noted: "
            f"{'; '.join(sfo.get('pain_points') or []) or 'n/a'}.\n"
            f"Current JTC services: {', '.join(sfo.get('current_services') or []) or 'none'}.")


def seed_documents(sfo_ids: list[int], rng: random.Random) -> int:
    """Write ACTUAL fake document files into the doc store (downloadable demos)."""
    from storage.docstore import get_docstore
    ds = get_docstore()
    n = 0
    for sid in rng.sample(sfo_ids, k=min(len(sfo_ids), max(6, len(sfo_ids) // 3))):
        sfo = store.get_sfo(sid)
        for tmpl, dtype in rng.sample(DOC_TEMPLATES, k=rng.randint(1, 3)):
            name = tmpl.format(yr=2025, fam=sfo.get("family_name", "Family"),
                               q=rng.randint(1, 4)).replace(".pdf", ".txt")
            body = _doc_text(sfo, dtype).encode()
            try:
                key = ds.put(sid, name, body)
            except Exception:  # noqa: BLE001 — fall back to a metadata key
                key = f"seed/{sid}/{name}"
            store.add_document({"sfo_id": sid, "name": name, "doc_type": dtype,
                                "storage_key": key, "byte_size": len(body),
                                "content_text": body.decode(), "uploaded_by": "seed"})
            n += 1
    return n


def seed_actions(sfo_ids: list[int], rng: random.Random) -> int:
    """Schedule next-actions tied to accepted/presented recommendations."""
    from datetime import date, timedelta
    n = 0
    for sid in sfo_ids:
        recs = store.list_recommendations(sfo_id=sid)
        for r in recs:
            if r["status"] in ("accepted", "booked"):
                due = (date.today() + timedelta(days=rng.randint(-10, 45))).isoformat()
                store.upsert_next_action({
                    "sfo_id": sid, "recommendation_id": r["id"], "kind": "consultation",
                    "title": f"Consultation — {r['service_name']}", "due_date": due,
                    "status": "open"})
                n += 1
            elif r["status"] == "presented" and rng.random() < 0.5:
                due = (date.today() + timedelta(days=rng.randint(1, 30))).isoformat()
                store.upsert_next_action({
                    "sfo_id": sid, "recommendation_id": r["id"], "kind": "follow_up",
                    "title": f"Follow up — {r['service_name']}", "due_date": due,
                    "status": "open"})
                n += 1
    return n


def seed_conversations(sfo_ids: list[int], rng: random.Random) -> int:
    """Realistic intake→upsell conversation logs for a sample of families."""
    email = os.environ.get("ADMIN_EMAIL", "admin@fastfund.org")
    n = 0
    for sid in rng.sample(sfo_ids, k=min(12, len(sfo_ids))):
        sfo = store.get_sfo(sid)
        script = rng.choice(CONVO_SCRIPTS)
        cid = store.create_conversation(email, sfo_id=sid, title=f"Intro · {sfo['name']}")
        for role, text in script:
            store.add_message(cid, role, text.format(gens=sfo.get("generations", 2)))
        n += 1
    return n


def run_seed(count: int = 100, seed: int = 42) -> dict:
    """Programmatic full seed (services + SFOs + members + funnel + actions +
    documents + conversations). Used by the CLI and by the app's auto-seed."""
    from faker import Faker
    store.init_db()
    rng = random.Random(seed)
    fake = Faker()
    Faker.seed(seed)
    seed_services()
    ids = seed_sfos(count, rng)
    seed_members(ids, rng, fake)
    seed_funnel(ids, rng)
    seed_actions(ids, rng)
    seed_portfolio(ids, rng)
    seed_documents(ids, rng)
    seed_conversations(ids, rng)
    store.spread_demo_timestamps(90)
    return store.stats()


def autoseed_if_empty(count: int = 100, seed: int = 42) -> bool:
    """Seed only when the store has no SFOs yet — safe to call on every boot.
    Returns True if it seeded. Gated by the caller (e.g. SFOHUB_AUTOSEED=1)."""
    try:
        if store.count_sfos() > 0:
            return False
        run_seed(count, seed)
        return True
    except Exception:  # noqa: BLE001 — never let seeding crash app startup
        return False


def main():
    ap = argparse.ArgumentParser(description="Seed FastFund demo data")
    ap.add_argument("--count", type=int, default=100, help="number of synthetic SFOs")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    ap.add_argument("--services-only", action="store_true",
                    help="only seed the service catalogue + cross-sell graph")
    args = ap.parse_args()

    store.init_db()
    rng = random.Random(args.seed)

    n_svc = seed_services()
    print(f"✓ services: {n_svc} + cross-sell graph")
    if args.services_only:
        print(store.stats())
        return

    from faker import Faker
    fake = Faker()
    Faker.seed(args.seed)

    ids = seed_sfos(args.count, rng)
    print(f"✓ SFOs: {len(ids)}")
    n_mem = seed_members(ids, rng, fake)
    print(f"✓ family members: {n_mem}")
    n_recs = seed_funnel(ids, rng)
    print(f"✓ recommendations: {n_recs}")
    n_act = seed_actions(ids, rng)
    print(f"✓ next actions: {n_act}")
    nh, nt = seed_portfolio(ids, rng)
    print(f"✓ holdings: {nh} · transactions: {nt}")
    n_doc = seed_documents(ids, rng)
    print(f"✓ documents: {n_doc}")
    n_conv = seed_conversations(ids, rng)
    print(f"✓ conversations: {n_conv}")
    store.spread_demo_timestamps(90)
    print("✓ backdated demo timestamps")
    print(store.stats())


if __name__ == "__main__":
    main()
