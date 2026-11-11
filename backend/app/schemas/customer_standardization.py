"""Customer Standardization Workbench request schemas."""

from typing import List, Optional

from pydantic import BaseModel, Field


class CustomerApprovalItem(BaseModel):
    raw_customer: str = Field(..., min_length=1, max_length=255)
    standard_customer: str = Field(..., min_length=1, max_length=255)
    confidence: Optional[float] = Field(None, ge=0, le=100)
    # Required to replace an existing approved mapping with a different value.
    replace_existing: bool = False


class CustomerApprovalRequest(BaseModel):
    email_id: int = Field(..., ge=1)
    attachment_id: Optional[int] = Field(None, ge=1)
    items: List[CustomerApprovalItem] = Field(..., min_length=1, max_length=500)
