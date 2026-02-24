"""Seed the database with sample data for testing.

Run once:  python -m db.seed
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from db.engine import AsyncSessionLocal, init_db
from db.models import Customer, Service, ServiceIncident


async def seed() -> None:
    await init_db()

    async with AsyncSessionLocal() as session:
        # Skip if data already exists
        result = await session.execute(select(Customer).where(Customer.account_id == "ACC-JT001"))
        if result.scalar_one_or_none() is not None:
            print("Seed data already present — skipping.")
            return

        customer = Customer(
            account_id="ACC-JT001",
            full_name="Julio Triana",
            phone_number="+14372455896",
            email="julio@example.com",
            account_type="residential",
            account_status="active",
        )
        session.add(customer)
        await session.flush()

        internet = Service(
            account_id="ACC-JT001",
            service_type="internet",
            plan_name="Gigabit 1000",
            status="degraded",
        )
        phone = Service(
            account_id="ACC-JT001",
            service_type="phone",
            plan_name="Unlimited Talk",
            status="active",
        )
        session.add_all([internet, phone])
        await session.flush()

        incident = ServiceIncident(
            account_id="ACC-JT001",
            service_id=internet.service_id,
            title="Intermittent internet drops in your area",
            description=(
                "Our network team has identified intermittent packet loss affecting "
                "Gigabit customers in your area since approximately 6:00 AM today. "
                "Engineers are actively working on a fix. Estimated resolution: 2 hours."
            ),
            severity="high",
            status="investigating",
        )
        session.add(incident)
        await session.commit()

        print("Seed complete:")
        print(f"  Customer : ACC-JT001 — Julio Triana (+14372455896)")
        print(f"  Services : internet (degraded), phone (active)")
        print(f"  Incident : {incident.title}")


if __name__ == "__main__":
    asyncio.run(seed())
