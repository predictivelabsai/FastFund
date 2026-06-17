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


def seed_conversations(sfo_ids: list[int], rng: random.Random) -> int:
    """A couple of illustrative conversation logs (inquiry → upsell)."""
    email = os.environ.get("ADMIN_EMAIL", "admin@jtcgroup.com")
    n = 0
    for sid in rng.sample(sfo_ids, k=min(8, len(sfo_ids))):
        sfo = store.get_sfo(sid)
        cid = store.create_conversation(email, sfo_id=sid,
                                        title=f"Intro · {sfo['name']}")
        store.add_message(cid, "user", "Tell me about our family's current governance setup.")
        store.add_message(cid, "assistant",
                          "Happy to. Based on your profile I can see the structures "
                          "in place and where a formal governance framework would help "
                          "the rising generation. Shall I outline the options?")
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="Seed SFO Hub demo data")
    ap.add_argument("--count", type=int, default=60, help="number of synthetic SFOs")
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

    ids = seed_sfos(args.count, rng)
    print(f"✓ SFOs: {len(ids)}")
    n_recs = seed_funnel(ids, rng)
    print(f"✓ recommendations: {n_recs}")
    n_conv = seed_conversations(ids, rng)
    print(f"✓ conversations: {n_conv}")
    print(store.stats())


if __name__ == "__main__":
    main()
