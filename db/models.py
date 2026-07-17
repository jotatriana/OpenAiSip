"""SQLAlchemy ORM models for the OpenAI SIP Bridge."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
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
    mailing_address: Mapped[str | None] = mapped_column(String, nullable=True)
    preferred_contact_method: Mapped[str] = mapped_column(String, default="phone")  # phone | email | sms
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


class CallTranscript(Base):
    """One row per spoken turn in a call.

    PCI NOTE: text is scrubbed of obvious card-number patterns before insert.
    A production deployment should apply a certified PII detection service.
    """
    __tablename__ = "call_transcripts"
    __table_args__ = (
        Index("ix_call_transcripts_call_id", "call_id"),
        Index("ix_call_transcripts_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(String, nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)   # 'caller' | 'assistant'
    text: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CallEvent(Base):
    """Append-only event log for a single call.

    One row per notable event: phase transitions, tool calls, WS reconnects, etc.
    Complements the CDR snapshot with a detailed timeline for debugging and audit.
    """
    __tablename__ = "call_events"
    __table_args__ = (
        Index("ix_call_events_call_id", "call_id"),
        Index("ix_call_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # see EVENT_* constants
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON payload
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EscalationContext(Base):
    """Structured handoff packet written when the AI escalates a call to a human agent.

    The receiving agent's desktop app reads this by call_id or sip_call_id to get
    a full briefing without replaying the conversation from scratch.
    """
    __tablename__ = "escalation_contexts"
    __table_args__ = (
        Index("ix_escalation_contexts_created_at", "created_at"),
    )

    call_id: Mapped[str] = mapped_column(String, primary_key=True)
    sip_call_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    account_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    caller_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    caller_number: Mapped[str] = mapped_column(String, nullable=False, default="")
    phase_at_escalation: Mapped[str | None] = mapped_column(String, nullable=True)
    escalation_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    frustration_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    # Last N transcript turns serialised as JSON for quick display
    recent_transcript: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CallDetailRecord(Base):
    """Persisted call summary written at call end. Survives process restarts.

    One row per call. Captures billing-relevant fields and outcome.
    """
    __tablename__ = "call_detail_records"
    __table_args__ = (
        Index("ix_cdr_created_at", "created_at"),
        Index("ix_cdr_account_id", "account_id"),
    )

    call_id: Mapped[str] = mapped_column(String, primary_key=True)
    sip_call_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    from_uri: Mapped[str] = mapped_column(String, nullable=False, default="")
    to_uri: Mapped[str] = mapped_column(String, nullable=False, default="")
    caller_number: Mapped[str] = mapped_column(String, nullable=False, default="")
    account_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    state: Mapped[str] = mapped_column(String, nullable=False)          # ENDED | FAILED
    phase_at_end: Mapped[str | None] = mapped_column(String, nullable=True)
    service_category: Mapped[str | None] = mapped_column(String, nullable=True)
    hangup_cause: Mapped[str | None] = mapped_column(String, nullable=True)
    escalated: Mapped[int] = mapped_column(Integer, default=0)          # 0/1 bool
    frustration_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_audio_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_audio_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BillingAccount(Base):
    """Current billing snapshot for a customer — one row per account."""
    __tablename__ = "billing_accounts"

    account_id: Mapped[str] = mapped_column(String, ForeignKey("customers.account_id"), primary_key=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    minimum_payment_due: Mapped[float] = mapped_column(Float, default=0.0)
    due_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Payment(Base):
    """A single historical payment. method is a masked display string, never a real card number."""
    __tablename__ = "payments"

    payment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("customers.account_id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)   # e.g. "Visa ····4432"
    status: Mapped[str] = mapped_column(String, default="completed")
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Product(Base):
    """Global service plan catalog — not customer-specific."""
    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String, primary_key=True)   # e.g. PLAN-INT-100
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)       # internet | tv | phone | mobile | bundle
    price_monthly: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(String, default="both")   # residential | business | both


class Promotion(Base):
    """Active promotional offers, filtered by account_type when presented to a caller."""
    __tablename__ = "promotions"

    promotion_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[str] = mapped_column(String, default="both")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ServiceArea(Base):
    """Mock service-availability lookup, keyed by a zip/postal prefix extracted from the caller's address."""
    __tablename__ = "service_areas"

    zip_prefix: Mapped[str] = mapped_column(String, primary_key=True)   # e.g. "900"
    eligible: Mapped[int] = mapped_column(Integer, default=1)           # 0/1 bool
    available_plans: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list of product_ids
    estimated_install_days: Mapped[int] = mapped_column(Integer, default=5)


class Appointment(Base):
    """A scheduled technician visit."""
    __tablename__ = "appointments"

    appointment_id: Mapped[str] = mapped_column(String, primary_key=True)   # e.g. APT-00000001
    account_id: Mapped[str] = mapped_column(String, ForeignKey("customers.account_id"), nullable=False)
    scheduled_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    time_window: Mapped[str] = mapped_column(String, nullable=False)        # e.g. "10am-12pm"
    appointment_type: Mapped[str] = mapped_column(String, nullable=False)   # installation | repair | upgrade
    status: Mapped[str] = mapped_column(String, default="scheduled")        # scheduled | confirmed | completed | cancelled
