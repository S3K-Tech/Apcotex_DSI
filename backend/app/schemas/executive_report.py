"""Request schemas for quarterly executive reports."""

from typing import List, Optional

from pydantic import BaseModel, Field


class ReportFilters(BaseModel):
    """Same fields as the Visualizations analytics query."""

    distributor_id: Optional[int] = None
    distributor: Optional[str] = Field(None, max_length=255)
    customer: Optional[str] = Field(None, max_length=255)
    product: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    segment: Optional[str] = Field(None, max_length=255)
    fiscal_year_start: Optional[int] = None
    period: Optional[str] = Field("full_year", max_length=40)
    start_month: Optional[str] = Field(None, max_length=20)
    end_month: Optional[str] = Field(None, max_length=20)


class GenerateReportRequest(BaseModel):
    filters: ReportFilters


class RecipientIn(BaseModel):
    role: Optional[str] = Field(None, max_length=20)
    name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=320)


class SaveRecipientsRequest(BaseModel):
    recipients: List[RecipientIn] = Field(..., max_length=10)


class SendReportEmailRequest(BaseModel):
    filters: ReportFilters
    recipients: List[RecipientIn] = Field(..., min_length=1, max_length=50)
    idempotency_key: str = Field(..., min_length=8, max_length=64)
