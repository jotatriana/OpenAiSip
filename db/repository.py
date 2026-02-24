"""Async query functions used by the tool executor."""
from __future__ import annotations

import random
import string
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import AsyncSessionLocal
from db.models import Customer, ServiceIncident, SupportTicket


async def find_customer(identifier: str, identifier_type: str) -> dict | None:
    """Look up a customer by phone number or account ID.

    Returns a dict with account details, or None if not found.
    """
    async with AsyncSessionLocal() as session:
        if identifier_type == "phone":
            stmt = select(Customer).where(Customer.phone_number == identifier)
        else:
            stmt = select(Customer).where(Customer.account_id == identifier)

        result = await session.execute(stmt)
        customer = result.scalar_one_or_none()

        if customer is None:
            return None

        return {
            "account_id": customer.account_id,
            "full_name": customer.full_name,
            "phone_number": customer.phone_number,
            "email": customer.email or "",
            "account_type": customer.account_type,
            "account_status": customer.account_status,
        }


async def get_service_status(account_id: str) -> dict:
    """Return all services and any open incidents for an account."""
    async with AsyncSessionLocal() as session:
        # Verify account exists
        customer = await session.get(Customer, account_id)
        if customer is None:
            return {"error": f"Account {account_id} not found"}

        services = [
            {
                "service_type": svc.service_type,
                "plan_name": svc.plan_name,
                "status": svc.status,
            }
            for svc in customer.services
        ]

        stmt = (
            select(ServiceIncident)
            .where(
                ServiceIncident.account_id == account_id,
                ServiceIncident.status != "resolved",
            )
            .order_by(ServiceIncident.created_at.desc())
        )
        result = await session.execute(stmt)
        open_incidents = result.scalars().all()

        incidents = [
            {
                "incident_id": inc.incident_id,
                "title": inc.title,
                "description": inc.description,
                "severity": inc.severity,
                "status": inc.status,
                "opened_at": inc.created_at.isoformat(),
            }
            for inc in open_incidents
        ]

        return {
            "account_id": account_id,
            "services": services,
            "open_incidents": incidents,
        }


async def create_ticket(
    account_id: str,
    issue_summary: str,
    priority: str,
    call_id: str = "",
) -> dict:
    """Insert a new support ticket and return its ID."""
    async with AsyncSessionLocal() as session:
        ticket_id = "TKT-" + "".join(random.choices(string.digits, k=8))
        ticket = SupportTicket(
            ticket_id=ticket_id,
            account_id=account_id,
            call_id=call_id or None,
            issue_summary=issue_summary,
            priority=priority,
            status="open",
            created_by="ai",
        )
        session.add(ticket)
        await session.commit()

        return {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "priority": priority,
            "status": "created",
        }
