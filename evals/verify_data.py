"""Compute ground-truth values from the canonical seeded FastFund DB.

The deployed demo auto-seeds with run_seed(count=100, seed=42); seeding locally
with the same args reproduces identical data, so the figures printed here are the
expected_answer values used in evals/ground_truth.csv. Re-run after changing the
seeder to refresh the ground truth.

Usage:
    python -m data.synth ... (or) python -c "from data import synth; synth.run_seed(100,42)"
    DB_URL=sqlite:///fastfund.db python evals/verify_data.py
"""
import os
import sqlalchemy

DB_URL = os.environ.get("DB_URL", "sqlite:///fastfund.db")
engine = sqlalchemy.create_engine(DB_URL)


def main():
    with engine.connect() as c:
        def q1(sql):
            return c.execute(sqlalchemy.text(sql)).scalar()

        def q(sql):
            return c.execute(sqlalchemy.text(sql)).fetchall()

        print("=== SFO COUNTS ===")
        print("total:", q1("SELECT COUNT(*) FROM sfos"))
        for st in ("client", "lead", "onboarding"):
            print(f"{st}:", q1(f"SELECT COUNT(*) FROM sfos WHERE stage='{st}'"))
        print("aum>1bn:", q1("SELECT COUNT(*) FROM sfos WHERE aum_usd>1000000000"))
        print("aum>2bn:", q1("SELECT COUNT(*) FROM sfos WHERE aum_usd>2000000000"))
        print("3+ generations:", q1("SELECT COUNT(*) FROM sfos WHERE generations>=3"))
        print("domicile JE:", q1("SELECT COUNT(*) FROM sfos WHERE domicile='JE'"))
        print("domicile KY:", q1("SELECT COUNT(*) FROM sfos WHERE domicile='KY'"))
        print("hold trusts:", q1("SELECT COUNT(*) FROM sfos WHERE current_services LIKE '%\"trusts\"%'"))
        print("hold edge:", q1("SELECT COUNT(*) FROM sfos WHERE current_services LIKE '%\"edge\"%'"))

        print("\n=== AUM / ALLOCATION ===")
        print("total AUM:", q1("SELECT ROUND(SUM(aum_usd)) FROM sfos"))
        print("avg AUM:", q1("SELECT ROUND(AVG(aum_usd)) FROM sfos"))
        print("max AUM:", q1("SELECT ROUND(MAX(aum_usd)) FROM sfos"))
        print("avg PE %:", q1("SELECT ROUND(AVG(json_extract(asset_mix,'$.private_equity')),1) FROM sfos"))
        print("avg RE %:", q1("SELECT ROUND(AVG(json_extract(asset_mix,'$.real_estate')),1) FROM sfos"))
        print("top AUM:", q("SELECT name, ROUND(aum_usd) FROM sfos ORDER BY aum_usd DESC LIMIT 3"))

        print("\n=== SERVICES ===")
        print("services:", q1("SELECT COUNT(*) FROM services"))
        print("premium services:", q1("SELECT COUNT(*) FROM services WHERE tier='premium'"))

        print("\n=== RECOMMENDATIONS / FUNNEL ===")
        print("total recs:", q1("SELECT COUNT(*) FROM recommendations"))
        for st in ("suggested", "presented", "accepted", "booked", "declined"):
            print(f"{st}:", q1(f"SELECT COUNT(*) FROM recommendations WHERE status='{st}'"))
        print("cross_sell:", q1("SELECT COUNT(*) FROM recommendations WHERE kind='cross_sell'"))
        print("upsell:", q1("SELECT COUNT(*) FROM recommendations WHERE kind='upsell'"))
        print("pipeline value (acc+booked):",
              q1("SELECT ROUND(SUM(est_value_usd)) FROM recommendations WHERE status IN ('accepted','booked')"))
        print("most recommended:",
              q("SELECT s.name, COUNT(*) n FROM recommendations r JOIN services s ON s.id=r.service_id GROUP BY s.id ORDER BY n DESC LIMIT 3"))

        print("\n=== MEMBERS / ACTIONS / DOCS ===")
        print("family members:", q1("SELECT COUNT(*) FROM family_members"))
        print("next_actions:", q1("SELECT COUNT(*) FROM next_actions"))
        print("open actions:", q1("SELECT COUNT(*) FROM next_actions WHERE status='open'"))
        print("documents:", q1("SELECT COUNT(*) FROM documents"))
        print("conversations:", q1("SELECT COUNT(*) FROM conversations"))


if __name__ == "__main__":
    main()
