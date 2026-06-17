"""Synthetic data generator + seeder for SFO Hub.

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


def seed_documents(sfo_ids: list[int], rng: random.Random) -> int:
    n = 0
    for sid in rng.sample(sfo_ids, k=min(len(sfo_ids), max(6, len(sfo_ids) // 3))):
        sfo = store.get_sfo(sid)
        for tmpl, dtype in rng.sample(DOC_TEMPLATES, k=rng.randint(1, 3)):
            name = tmpl.format(yr=2025, fam=sfo.get("family_name", "Family"),
                               q=rng.randint(1, 4))
            store.add_document({"sfo_id": sid, "name": name, "doc_type": dtype,
                                "storage_key": f"seed/{sid}/{name}",
                                "byte_size": rng.randint(80, 900) * 1024,
                                "content_text": "", "uploaded_by": "seed"})
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
    email = os.environ.get("ADMIN_EMAIL", "admin@jtcgroup.com")
    n = 0
    for sid in rng.sample(sfo_ids, k=min(12, len(sfo_ids))):
        sfo = store.get_sfo(sid)
        script = rng.choice(CONVO_SCRIPTS)
        cid = store.create_conversation(email, sfo_id=sid, title=f"Intro · {sfo['name']}")
        for role, text in script:
            store.add_message(cid, role, text.format(gens=sfo.get("generations", 2)))
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="Seed SFO Hub demo data")
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
    n_doc = seed_documents(ids, rng)
    print(f"✓ documents: {n_doc}")
    n_conv = seed_conversations(ids, rng)
    print(f"✓ conversations: {n_conv}")
    print(store.stats())


if __name__ == "__main__":
    main()
