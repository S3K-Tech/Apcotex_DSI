"""Request schemas for FG Master and product standardization endpoints."""

from typing import List, Optional

from pydantic import BaseModel, Field


class FGMasterPublishRequest(BaseModel):
    confirm: bool = Field(..., description="Must be true: explicit publish confirmation")


class ProductApprovalItem(BaseModel):
    raw_product: str = Field(..., min_length=1, max_length=255)
    standard_product: str = Field(..., min_length=1, max_length=255)
    confidence: Optional[float] = Field(None, ge=0, le=100)


class ProductApprovalRequest(BaseModel):
    email_id: int = Field(..., ge=1)
    attachment_id: Optional[int] = Field(None, ge=1)
    items: List[ProductApprovalItem] = Field(..., min_length=1, max_length=500)
