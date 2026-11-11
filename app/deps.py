import hmac
import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.clock import Clock, get_clock
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import Forbidden, MemberRequired, StaffNotConfigured, StaffRequired


def get_member_id(x_member_id: Annotated[str | None, Header()] = None) -> uuid.UUID:
    """Who is calling. There is no login yet: the caller names themselves in a header."""
    if x_member_id is None:
        raise MemberRequired("send your member id in the X-Member-Id header")
    try:
        return uuid.UUID(x_member_id)
    except ValueError:
        raise MemberRequired("X-Member-Id must be a valid UUID") from None


def require_staff(
    settings: Annotated[Settings, Depends(get_settings)],
    x_staff_key: Annotated[str | None, Header()] = None,
) -> None:
    """Staff endpoints need the shared key in the X-Staff-Key header. Fails closed."""
    if not settings.staff_api_key:
        raise StaffNotConfigured("staff access is not configured: set STAFF_API_KEY")
    if x_staff_key is None:
        raise StaffRequired("send the staff key in the X-Staff-Key header")
    if not hmac.compare_digest(x_staff_key.encode(), settings.staff_api_key.encode()):
        raise Forbidden("that is not a valid staff key")


DbSession = Annotated[Session, Depends(get_session)]
ClockDep = Annotated[Clock, Depends(get_clock)]
MemberId = Annotated[uuid.UUID, Depends(get_member_id)]
