"""SQLAlchemy ORM models for the OpenAI SIP Bridge."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.engine import Base


class Customer(Base):
    __tablename__ = "customers"

    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    phone_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    account_type: Mapped[str] = mapped_column(String, default="residential")  # residential | business
    account_status: Mapped[str] = mapped_column(String, default="active")     # active | suspended | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    services: Mapped[list[Service]] = relationship("Service", back_populates="customer", lazy="selectin")
    incidents: Mapped[list[ServiceIncident]] = relationship("ServiceIncident", back_populates="customer", lazy="selectin")
    tickets: Mapped[list[SupportTicket]] = relationship("SupportTicket", back_populates="customer", lazy="selectin")


class Service(Base):
    __tablename__ = "services"

    service_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("customers.account_id"), nullable=False)
    service_type: Mapped[str] = mapped_column(String, nullable=False)  # internet | phone | tv | mobile
    plan_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")      # active | degraded | outage | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    customer: Mapped[Customer] = relationship("Customer", back_populates="services")
    incidents: Mapped[list[ServiceIncident]] = relationship("ServiceIncident", back_populates="service", lazy="selectin")


class ServiceIncident(Base):
    __tablename__ = "service_incidents"

    incident_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("customers.account_id"), nullable=False)
    service_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("services.service_id"), nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String, default="medium")    # low | medium | high | critical
    status: Mapped[str] = mapped_column(String, default="open")        # open | investigating | resolved
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    customer: Mapped[Customer] = relationship("Customer", back_populates="incidents")
    service: Mapped[Service | None] = relationship("Service", back_populates="incidents")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    ticket_id: Mapped[str] = mapped_column(String, primary_key=True)   # e.g. TKT-00000001
    account_id: Mapped[str] = mapped_column(String, ForeignKey("customers.account_id"), nullable=False)
    call_id: Mapped[str | None] = mapped_column(String, nullable=True)  # links to in-memory call
    issue_summary: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String, nullable=False)       # low | medium | high | critical
    status: Mapped[str] = mapped_column(String, default="open")         # open | in_progress | resolved | closed
    created_by: Mapped[str] = mapped_column(String, default="ai")       # ai | agent | customer
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    customer: Mapped[Customer] = relationship("Customer", back_populates="tickets")
