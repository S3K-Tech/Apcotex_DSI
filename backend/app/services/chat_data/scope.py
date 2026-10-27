"""Server-resolved data scope for one Chat with Data request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.services.access_control_service import DataScope

RESTRICTED_SEGMENT_MESSAGE = (
    "Restricted access: You do not have permission to access data for this segment."
)


@dataclass(frozen=True)
class ChatScope:
    """Mirrors ``segment_scope_for_user`` / ``distributor_companies_scope_for_user``.

    ``None`` means unrestricted on that axis; an empty list means no access.
    """

    unrestricted: bool
    access_mode: str
    allowed_segments: Optional[List[str]] = None
    allowed_companies: Optional[List[str]] = None
    user_id: Optional[int] = None
    role: str = ""
    user_name: str = ""

    @classmethod
    def from_data_scope(
        cls,
        scope: DataScope,
        *,
        user_id: Optional[int] = None,
        role: str = "",
        user_name: str = "",
    ) -> "ChatScope":
        if scope.unrestricted:
            return cls(True, scope.access_mode, None, None, user_id, role, user_name)
        if scope.access_mode == "distributor":
            return cls(
                False,
                scope.access_mode,
                None,
                list(scope.allowed_companies or []),
                user_id,
                role,
                user_name,
            )
        return cls(
            False,
            scope.access_mode,
            list(scope.allowed_segments or []),
            None,
            user_id,
            role,
            user_name,
        )

    def segment_allowed(self, segment: str) -> bool:
        if self.allowed_segments is None:
            return True
        folded = (segment or "").strip().casefold()
        return any(folded == (s or "").strip().casefold() for s in self.allowed_segments)

    @property
    def has_no_access(self) -> bool:
        return (self.allowed_segments is not None and not self.allowed_segments) or (
            self.allowed_companies is not None and not self.allowed_companies
        )

    def describe(self) -> str:
        if self.unrestricted:
            return "All data (administrator access)."
        if self.allowed_companies is not None:
            return "Limited to your assigned distributors."
        if self.allowed_segments:
            return "Limited to your segments: " + ", ".join(sorted(self.allowed_segments)) + "."
        return "No data access is assigned to your account."
