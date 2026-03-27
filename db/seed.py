"""Seed the database with sample data for testing.

Run once:  python -m db.seed
Re-seed:   python -m db.seed --reset   (drops all rows first)
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete

from db.engine import AsyncSessionLocal, init_db
from db.models import Customer, Service, ServiceIncident, SupportTicket

# ── Customer definitions ───────────────────────────────────────────────────────

CUSTOMERS = [
    {
        "account_id": "ACC-JT001",
        "full_name": "Julio Triana",
        "phone_number": "+14168489468",
        "email": "julio@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-SM002",
        "full_name": "Sarah Mitchell",
        "phone_number": "+14165550102",
        "email": "sarah.mitchell@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-RG003",
        "full_name": "Robert Garcia",
        "phone_number": "+16475550193",
        "email": "rgarcia@example.com",
        "account_type": "business",
        "account_status": "active",
    },
    {
        "account_id": "ACC-LP004",
        "full_name": "Linda Park",
        "phone_number": "+14085550174",
        "email": "linda.park@example.com",
        "account_type": "residential",
        "account_status": "suspended",
    },
    {
        "account_id": "ACC-DW005",
        "full_name": "David Williams",
        "phone_number": "+17185550145",
        "email": "dwilliams@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-AC006",
        "full_name": "Angela Chen",
        "phone_number": "+16045550116",
        "email": "achen@example.com",
        "account_type": "business",
        "account_status": "active",
    },
    {
        "account_id": "ACC-MK007",
        "full_name": "Michael Kim",
        "phone_number": "+13125550127",
        "email": "m.kim@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-FB008",
        "full_name": "Fatima Bello",
        "phone_number": "+14255550138",
        "email": "fatima.bello@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-TR009",
        "full_name": "Thomas Rivera",
        "phone_number": "+15145550199",
        "email": "t.rivera@example.com",
        "account_type": "business",
        "account_status": "active",
    },
    {
        "account_id": "ACC-NO010",
        "full_name": "Natalie Okafor",
        "phone_number": "+16135550160",
        "email": "nokafor@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-JL011",
        "full_name": "James Lee",
        "phone_number": "+17025550111",
        "email": "james.lee@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-PV012",
        "full_name": "Patricia Vasquez",
        "phone_number": "+15025550122",
        "email": "pvasquez@example.com",
        "account_type": "business",
        "account_status": "active",
    },
    {
        "account_id": "ACC-CN013",
        "full_name": "Carlos Nguyen",
        "phone_number": "+16265550133",
        "email": "carlos.nguyen@example.com",
        "account_type": "residential",
        "account_status": "cancelled",
    },
    {
        "account_id": "ACC-EH014",
        "full_name": "Emily Hassan",
        "phone_number": "+14035550144",
        "email": "ehassan@example.com",
        "account_type": "residential",
        "account_status": "active",
    },
    {
        "account_id": "ACC-BT015",
        "full_name": "Brian Thompson",
        "phone_number": "+19055550155",
        "email": "b.thompson@example.com",
        "account_type": "business",
        "account_status": "active",
    },
]

# ── Services per account ───────────────────────────────────────────────────────

SERVICES = [
    # ACC-JT001 — Julio Triana
    {"account_id": "ACC-JT001", "service_type": "internet", "plan_name": "Gigabit 1000",    "status": "degraded"},
    {"account_id": "ACC-JT001", "service_type": "phone",    "plan_name": "Unlimited Talk",  "status": "active"},

    # ACC-SM002 — Sarah Mitchell
    {"account_id": "ACC-SM002", "service_type": "internet", "plan_name": "Fiber 500",        "status": "active"},
    {"account_id": "ACC-SM002", "service_type": "tv",       "plan_name": "Premium TV",       "status": "active"},
    {"account_id": "ACC-SM002", "service_type": "phone",    "plan_name": "Unlimited Talk",   "status": "active"},

    # ACC-RG003 — Robert Garcia (business)
    {"account_id": "ACC-RG003", "service_type": "internet", "plan_name": "Business 2Gbps",   "status": "outage"},
    {"account_id": "ACC-RG003", "service_type": "phone",    "plan_name": "Business Lines x5","status": "active"},

    # ACC-LP004 — Linda Park (suspended)
    {"account_id": "ACC-LP004", "service_type": "internet", "plan_name": "Fiber 100",        "status": "cancelled"},
    {"account_id": "ACC-LP004", "service_type": "phone",    "plan_name": "Basic Talk",       "status": "cancelled"},

    # ACC-DW005 — David Williams
    {"account_id": "ACC-DW005", "service_type": "internet", "plan_name": "Fiber 250",        "status": "active"},
    {"account_id": "ACC-DW005", "service_type": "mobile",   "plan_name": "Mobile Unlimited", "status": "active"},

    # ACC-AC006 — Angela Chen (business)
    {"account_id": "ACC-AC006", "service_type": "internet", "plan_name": "Business 1Gbps",   "status": "active"},
    {"account_id": "ACC-AC006", "service_type": "phone",    "plan_name": "Business Lines x3","status": "degraded"},
    {"account_id": "ACC-AC006", "service_type": "tv",       "plan_name": "Business TV",      "status": "active"},

    # ACC-MK007 — Michael Kim
    {"account_id": "ACC-MK007", "service_type": "internet", "plan_name": "Gigabit 1000",    "status": "active"},
    {"account_id": "ACC-MK007", "service_type": "tv",       "plan_name": "Basic TV",         "status": "active"},

    # ACC-FB008 — Fatima Bello
    {"account_id": "ACC-FB008", "service_type": "internet", "plan_name": "Fiber 500",        "status": "active"},
    {"account_id": "ACC-FB008", "service_type": "phone",    "plan_name": "Unlimited Talk",   "status": "active"},
    {"account_id": "ACC-FB008", "service_type": "mobile",   "plan_name": "Mobile Basic",     "status": "active"},

    # ACC-TR009 — Thomas Rivera (business)
    {"account_id": "ACC-TR009", "service_type": "internet", "plan_name": "Business 2Gbps",   "status": "active"},
    {"account_id": "ACC-TR009", "service_type": "phone",    "plan_name": "Business Lines x10","status": "active"},

    # ACC-NO010 — Natalie Okafor
    {"account_id": "ACC-NO010", "service_type": "internet", "plan_name": "Fiber 100",        "status": "active"},
    {"account_id": "ACC-NO010", "service_type": "tv",       "plan_name": "Premium TV",       "status": "degraded"},

    # ACC-JL011 — James Lee
    {"account_id": "ACC-JL011", "service_type": "internet", "plan_name": "Fiber 250",        "status": "active"},
    {"account_id": "ACC-JL011", "service_type": "phone",    "plan_name": "Basic Talk",       "status": "active"},

    # ACC-PV012 — Patricia Vasquez (business)
    {"account_id": "ACC-PV012", "service_type": "internet", "plan_name": "Business 1Gbps",   "status": "active"},
    {"account_id": "ACC-PV012", "service_type": "phone",    "plan_name": "Business Lines x2","status": "active"},

    # ACC-CN013 — Carlos Nguyen (cancelled)
    {"account_id": "ACC-CN013", "service_type": "internet", "plan_name": "Fiber 100",        "status": "cancelled"},

    # ACC-EH014 — Emily Hassan
    {"account_id": "ACC-EH014", "service_type": "internet", "plan_name": "Gigabit 1000",    "status": "active"},
    {"account_id": "ACC-EH014", "service_type": "mobile",   "plan_name": "Mobile Unlimited", "status": "active"},

    # ACC-BT015 — Brian Thompson (business)
    {"account_id": "ACC-BT015", "service_type": "internet", "plan_name": "Business 2Gbps",   "status": "active"},
    {"account_id": "ACC-BT015", "service_type": "phone",    "plan_name": "Business Lines x5","status": "active"},
    {"account_id": "ACC-BT015", "service_type": "tv",       "plan_name": "Business TV",      "status": "active"},
]

# ── Incidents (keyed by account_id; service_type used to resolve service_id) ──

INCIDENTS = [
    {
        "account_id": "ACC-JT001",
        "service_type": "internet",
        "title": "Intermittent internet drops in your area",
        "description": (
            "Our network team has identified intermittent packet loss affecting Gigabit "
            "customers in your area since approximately 6:00 AM today. Engineers are "
            "actively working on a fix. Estimated resolution: 2 hours."
        ),
        "severity": "high",
        "status": "investigating",
    },
    {
        "account_id": "ACC-RG003",
        "service_type": "internet",
        "title": "Complete internet outage — business district node failure",
        "description": (
            "A fibre node serving your building has failed due to a hardware fault. "
            "A field technician has been dispatched and is on-site. "
            "Estimated restoration: 4 hours. We apologise for the disruption."
        ),
        "severity": "critical",
        "status": "open",
    },
    {
        "account_id": "ACC-AC006",
        "service_type": "phone",
        "title": "Intermittent call drops on business phone lines",
        "description": (
            "Some calls on your business lines are dropping after 5–10 minutes. "
            "This is caused by a SIP proxy misconfiguration during last night's maintenance. "
            "A fix has been deployed; monitoring is ongoing."
        ),
        "severity": "medium",
        "status": "investigating",
    },
    {
        "account_id": "ACC-NO010",
        "service_type": "tv",
        "title": "Premium TV channels showing pixelation",
        "description": (
            "Several HD channels on your Premium TV package are experiencing pixelation "
            "and brief freezes. This is linked to a signal amplifier issue in your area. "
            "Estimated resolution: tonight by 10 PM."
        ),
        "severity": "low",
        "status": "investigating",
    },
    {
        "account_id": "ACC-DW005",
        "service_type": "internet",
        "title": "Scheduled maintenance — brief outage expected",
        "description": (
            "We will be performing network upgrades in your area on Saturday between "
            "2:00 AM and 4:00 AM. You may experience up to 30 minutes of downtime. "
            "No action is required on your part."
        ),
        "severity": "low",
        "status": "open",
    },
    {
        "account_id": "ACC-FB008",
        "service_type": "internet",
        "title": "Slow speeds during peak hours",
        "description": (
            "Customers on the Fiber 500 plan in your neighbourhood have reported reduced "
            "speeds between 7 PM and 10 PM. Our capacity team is investigating and a "
            "network upgrade is scheduled for next week."
        ),
        "severity": "medium",
        "status": "investigating",
    },
]

# ── Pre-existing tickets ───────────────────────────────────────────────────────

TICKETS = [
    {
        "ticket_id": "TKT-00000001",
        "account_id": "ACC-SM002",
        "call_id": None,
        "issue_summary": "Customer reported TV remote not responding after firmware update.",
        "priority": "low",
        "status": "in_progress",
        "created_by": "agent",
    },
    {
        "ticket_id": "TKT-00000002",
        "account_id": "ACC-MK007",
        "call_id": None,
        "issue_summary": "Request to upgrade internet plan from Gigabit 1000 to Business 2Gbps.",
        "priority": "low",
        "status": "open",
        "created_by": "customer",
    },
    {
        "ticket_id": "TKT-00000003",
        "account_id": "ACC-RG003",
        "call_id": None,
        "issue_summary": "Complete internet outage — field technician dispatched.",
        "priority": "critical",
        "status": "in_progress",
        "created_by": "ai",
    },
    {
        "ticket_id": "TKT-00000004",
        "account_id": "ACC-LP004",
        "call_id": None,
        "issue_summary": "Customer requested account reactivation after payment received.",
        "priority": "medium",
        "status": "open",
        "created_by": "agent",
    },
    {
        "ticket_id": "TKT-00000005",
        "account_id": "ACC-EH014",
        "call_id": None,
        "issue_summary": "Mobile data not working after SIM swap at retail store.",
        "priority": "high",
        "status": "resolved",
        "created_by": "agent",
    },
]


# ── Seed function ──────────────────────────────────────────────────────────────

async def seed(reset: bool = False) -> None:
    await init_db()

    async with AsyncSessionLocal() as session:
        if reset:
            await session.execute(delete(SupportTicket))
            await session.execute(delete(ServiceIncident))
            await session.execute(delete(Service))
            await session.execute(delete(Customer))
            await session.commit()
            print("Existing data cleared.")
        else:
            from sqlalchemy import select
            result = await session.execute(select(Customer).where(Customer.account_id == "ACC-JT001"))
            if result.scalar_one_or_none() is not None:
                print("Seed data already present — skipping. Use --reset to re-seed.")
                return

        # Insert customers
        for c in CUSTOMERS:
            session.add(Customer(**c))
        await session.flush()

        # Insert services, collect service objects for incident FK resolution
        service_map: dict[tuple[str, str], Service] = {}
        for s in SERVICES:
            svc = Service(**s)
            session.add(svc)
            await session.flush()
            service_map[(s["account_id"], s["service_type"])] = svc

        # Insert incidents, resolving service_id from (account_id, service_type)
        for inc in INCIDENTS:
            service_type = inc.pop("service_type")
            svc = service_map.get((inc["account_id"], service_type))
            session.add(ServiceIncident(service_id=svc.service_id if svc else None, **inc))
            inc["service_type"] = service_type  # restore for idempotency

        # Insert pre-existing tickets
        for t in TICKETS:
            session.add(SupportTicket(**t))

        await session.commit()

    print(f"Seed complete: {len(CUSTOMERS)} customers, {len(SERVICES)} services, "
          f"{len(INCIDENTS)} incidents, {len(TICKETS)} tickets.")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    asyncio.run(seed(reset=reset))
