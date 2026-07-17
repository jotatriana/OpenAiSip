"""Seed the database with sample data for testing.

Run once:  python -m db.seed
Re-seed:   python -m db.seed --reset   (drops all rows first)
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete

from datetime import datetime

from db.engine import AsyncSessionLocal, init_db
from db.models import (
    Appointment,
    BillingAccount,
    Customer,
    Payment,
    Product,
    Promotion,
    Service,
    ServiceArea,
    ServiceIncident,
    SupportTicket,
)

# ── Customer definitions ───────────────────────────────────────────────────────

CUSTOMERS = [
    {
        "account_id": "ACC-JT001",
        "full_name": "Julio Triana",
        "phone_number": "+14168489468",
        "email": "julio@example.com",
        "account_type": "residential",
        "account_status": "active",
        "mailing_address": "123 Maple Street, Toronto, ON M5V 2T6",
    },
    {
        "account_id": "ACC-SM002",
        "full_name": "Sarah Mitchell",
        "phone_number": "+14165550102",
        "email": "sarah.mitchell@example.com",
        "account_type": "residential",
        "account_status": "active",
        "mailing_address": "456 Oak Avenue, Los Angeles, CA 90012",
        "preferred_contact_method": "email",
    },
    {
        "account_id": "ACC-RG003",
        "full_name": "Robert Garcia",
        "phone_number": "+16475550193",
        "email": "rgarcia@example.com",
        "account_type": "business",
        "account_status": "active",
        "mailing_address": "789 Business Park Dr, Suite 200, Toronto, ON M4B 1B3",
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
        "preferred_contact_method": "sms",
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

# ── Product catalog (global — not customer-specific) ───────────────────────────

PRODUCTS = [
    {"product_id": "PLAN-INT-100", "name": "Internet 100", "category": "internet",
     "price_monthly": 49.99, "account_type": "residential",
     "description": "100 Mbps download, 20 Mbps upload. Great for browsing and streaming."},
    {"product_id": "PLAN-INT-500", "name": "Fiber 500", "category": "internet",
     "price_monthly": 69.99, "account_type": "residential",
     "description": "500 Mbps symmetric fiber. Ideal for multiple devices and 4K streaming."},
    {"product_id": "PLAN-INT-GIG", "name": "Gigabit 1000", "category": "internet",
     "price_monthly": 89.99, "account_type": "residential",
     "description": "1000 Mbps symmetric fiber for power users and smart homes."},
    {"product_id": "PLAN-INT-BIZ2G", "name": "Business 2Gbps", "category": "internet",
     "price_monthly": 249.99, "account_type": "business",
     "description": "Dedicated 2 Gbps fiber with SLA-backed uptime for business locations."},
    {"product_id": "PLAN-TV-PREMIUM", "name": "Premium TV", "category": "tv",
     "price_monthly": 59.99, "account_type": "both",
     "description": "200+ channels including sports and premium movie packages."},
    {"product_id": "PLAN-MOBILE-UNL", "name": "Mobile Unlimited", "category": "mobile",
     "price_monthly": 45.00, "account_type": "both",
     "description": "Unlimited talk, text, and data with 5G access."},
    {"product_id": "PLAN-BUNDLE-HOME", "name": "Home Bundle", "category": "bundle",
     "price_monthly": 129.99, "account_type": "residential",
     "description": "Fiber 500 internet plus Premium TV, bundled and discounted."},
]

# ── Promotions ──────────────────────────────────────────────────────────────────

PROMOTIONS = [
    {"promotion_id": "PROMO-001", "title": "Bundle & Save",
     "description": "Save $15 a month when you bundle internet and TV for 12 months.",
     "account_type": "both", "expires_at": datetime(2026, 9, 30)},
    {"promotion_id": "PROMO-002", "title": "Gigabit Upgrade Special",
     "description": "Upgrade to Gigabit 1000 for the price of Fiber 500 for your first 6 months.",
     "account_type": "residential", "expires_at": datetime(2026, 8, 31)},
    {"promotion_id": "PROMO-003", "title": "Business Fiber Launch",
     "description": "New business fiber customers get their first month free.",
     "account_type": "business", "expires_at": datetime(2026, 10, 15)},
    {"promotion_id": "PROMO-004", "title": "Refer a Friend",
     "description": "Get a $50 account credit for every friend you refer who signs up.",
     "account_type": "both", "expires_at": None},
]

# ── Service areas (mock eligibility lookup, keyed by 3-digit zip/postal prefix) ─

SERVICE_AREAS = [
    {"zip_prefix": "100", "eligible": 1, "estimated_install_days": 3,
     "available_plans": '["PLAN-INT-100", "PLAN-INT-500", "PLAN-INT-GIG", "PLAN-TV-PREMIUM"]'},
    {"zip_prefix": "900", "eligible": 1, "estimated_install_days": 5,
     "available_plans": '["PLAN-INT-100", "PLAN-INT-500", "PLAN-TV-PREMIUM", "PLAN-MOBILE-UNL"]'},
    {"zip_prefix": "599", "eligible": 0, "estimated_install_days": 0, "available_plans": "[]"},
    {"zip_prefix": "888", "eligible": 0, "estimated_install_days": 0, "available_plans": "[]"},
]

# ── Billing accounts (one per customer, except ACC-CN013 — cancelled, no billing account on file) ─

BILLING_ACCOUNTS = [
    {"account_id": "ACC-JT001", "balance": 128.43, "minimum_payment_due": 45.00, "due_date": datetime(2026, 7, 25)},
    {"account_id": "ACC-SM002", "balance": 0.00, "minimum_payment_due": 0.00, "due_date": datetime(2026, 8, 1)},
    {"account_id": "ACC-RG003", "balance": 459.20, "minimum_payment_due": 150.00, "due_date": datetime(2026, 7, 20)},
    {"account_id": "ACC-LP004", "balance": 210.75, "minimum_payment_due": 210.75, "due_date": datetime(2026, 7, 10)},
    {"account_id": "ACC-DW005", "balance": 64.99, "minimum_payment_due": 64.99, "due_date": datetime(2026, 7, 28)},
    {"account_id": "ACC-AC006", "balance": 389.50, "minimum_payment_due": 120.00, "due_date": datetime(2026, 7, 22)},
    {"account_id": "ACC-MK007", "balance": 89.99, "minimum_payment_due": 89.99, "due_date": datetime(2026, 7, 30)},
    {"account_id": "ACC-FB008", "balance": 145.20, "minimum_payment_due": 50.00, "due_date": datetime(2026, 7, 26)},
    {"account_id": "ACC-TR009", "balance": 899.00, "minimum_payment_due": 300.00, "due_date": datetime(2026, 7, 21)},
    {"account_id": "ACC-NO010", "balance": 54.99, "minimum_payment_due": 54.99, "due_date": datetime(2026, 7, 29)},
    {"account_id": "ACC-JL011", "balance": 79.98, "minimum_payment_due": 79.98, "due_date": datetime(2026, 7, 27)},
    {"account_id": "ACC-PV012", "balance": 310.00, "minimum_payment_due": 100.00, "due_date": datetime(2026, 7, 23)},
    {"account_id": "ACC-EH014", "balance": 134.99, "minimum_payment_due": 45.00, "due_date": datetime(2026, 7, 24)},
    {"account_id": "ACC-BT015", "balance": 520.00, "minimum_payment_due": 175.00, "due_date": datetime(2026, 7, 19)},
]

# ── Payments (history per account — ACC-LP004 deliberately has none, despite a balance due) ────

PAYMENTS = [
    {"account_id": "ACC-JT001", "amount": 89.99, "method": "Visa ····4432", "paid_at": datetime(2026, 6, 18)},
    {"account_id": "ACC-JT001", "amount": 89.99, "method": "Visa ····4432", "paid_at": datetime(2026, 5, 18)},
    {"account_id": "ACC-SM002", "amount": 129.99, "method": "Mastercard ····7781", "paid_at": datetime(2026, 6, 15)},
    {"account_id": "ACC-SM002", "amount": 129.99, "method": "Mastercard ····7781", "paid_at": datetime(2026, 5, 15)},
    {"account_id": "ACC-RG003", "amount": 610.00, "method": "Business bank transfer", "paid_at": datetime(2026, 6, 20)},
    {"account_id": "ACC-RG003", "amount": 610.00, "method": "Business bank transfer", "paid_at": datetime(2026, 5, 20)},
    {"account_id": "ACC-DW005", "amount": 64.99, "method": "Amex ····1029", "paid_at": datetime(2026, 6, 28)},
    {"account_id": "ACC-AC006", "amount": 389.50, "method": "Business bank transfer", "paid_at": datetime(2026, 6, 22)},
    {"account_id": "ACC-AC006", "amount": 389.50, "method": "Business bank transfer", "paid_at": datetime(2026, 5, 22)},
    {"account_id": "ACC-MK007", "amount": 89.99, "method": "Visa ····5567", "paid_at": datetime(2026, 6, 30)},
    {"account_id": "ACC-FB008", "amount": 145.20, "method": "Mastercard ····3390", "paid_at": datetime(2026, 6, 26)},
    {"account_id": "ACC-TR009", "amount": 899.00, "method": "Business bank transfer", "paid_at": datetime(2026, 6, 21)},
    {"account_id": "ACC-TR009", "amount": 899.00, "method": "Business bank transfer", "paid_at": datetime(2026, 5, 21)},
    {"account_id": "ACC-NO010", "amount": 54.99, "method": "Visa ····2245", "paid_at": datetime(2026, 6, 29)},
    {"account_id": "ACC-JL011", "amount": 79.98, "method": "Mastercard ····6612", "paid_at": datetime(2026, 6, 27)},
    {"account_id": "ACC-PV012", "amount": 310.00, "method": "Business bank transfer", "paid_at": datetime(2026, 6, 23)},
    {"account_id": "ACC-EH014", "amount": 134.99, "method": "Visa ····8804", "paid_at": datetime(2026, 6, 24)},
    {"account_id": "ACC-BT015", "amount": 520.00, "method": "Business bank transfer", "paid_at": datetime(2026, 6, 19)},
    {"account_id": "ACC-BT015", "amount": 520.00, "method": "Business bank transfer", "paid_at": datetime(2026, 5, 19)},
]

# ── Appointments (only a subset of accounts have one scheduled) ────────────────

APPOINTMENTS = [
    {"appointment_id": "APT-00000001", "account_id": "ACC-JT001", "scheduled_date": datetime(2026, 7, 22),
     "time_window": "10am-12pm", "appointment_type": "repair", "status": "scheduled"},
    {"appointment_id": "APT-00000002", "account_id": "ACC-RG003", "scheduled_date": datetime(2026, 7, 19),
     "time_window": "2pm-4pm", "appointment_type": "repair", "status": "confirmed"},
    {"appointment_id": "APT-00000003", "account_id": "ACC-MK007", "scheduled_date": datetime(2026, 7, 25),
     "time_window": "10am-12pm", "appointment_type": "upgrade", "status": "scheduled"},
    {"appointment_id": "APT-00000004", "account_id": "ACC-BT015", "scheduled_date": datetime(2026, 7, 21),
     "time_window": "8am-10am", "appointment_type": "installation", "status": "confirmed"},
]


# ── Seed function ──────────────────────────────────────────────────────────────

async def seed(reset: bool = False) -> None:
    await init_db()

    async with AsyncSessionLocal() as session:
        if reset:
            await session.execute(delete(SupportTicket))
            await session.execute(delete(ServiceIncident))
            await session.execute(delete(Service))
            await session.execute(delete(Appointment))
            await session.execute(delete(Payment))
            await session.execute(delete(BillingAccount))
            await session.execute(delete(ServiceArea))
            await session.execute(delete(Promotion))
            await session.execute(delete(Product))
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

        # Insert product catalog, promotions, and service areas (no customer FK dependency)
        for p in PRODUCTS:
            session.add(Product(**p))
        for promo in PROMOTIONS:
            session.add(Promotion(**promo))
        for area in SERVICE_AREAS:
            session.add(ServiceArea(**area))

        # Insert billing accounts, payments, and appointments (account_id is a known string, no flush needed)
        for b in BILLING_ACCOUNTS:
            session.add(BillingAccount(**b))
        for pay in PAYMENTS:
            session.add(Payment(**pay))
        for appt in APPOINTMENTS:
            session.add(Appointment(**appt))

        await session.commit()

    print(f"Seed complete: {len(CUSTOMERS)} customers, {len(SERVICES)} services, "
          f"{len(INCIDENTS)} incidents, {len(TICKETS)} tickets, {len(PRODUCTS)} products, "
          f"{len(PROMOTIONS)} promotions, {len(SERVICE_AREAS)} service areas, "
          f"{len(BILLING_ACCOUNTS)} billing accounts, {len(PAYMENTS)} payments, "
          f"{len(APPOINTMENTS)} appointments.")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    asyncio.run(seed(reset=reset))
