import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.clock import Clock, get_clock
from app.db import get_session
from app.errors import MemberRequired


def get_member_id(x_member_id: Annotated[str | None, Header()] = None) -> uuid.UUID:
    """Who is calling. There is no login yet: the caller names themselves in a header."""
    if x_member_id is None:
        raise MemberRequired("send your member id in the X-Member-Id header")
    try:
        return uuid.UUID(x_member_id)
    except ValueError:
        raise MemberRequired("X-Member-Id must be a valid UUID") from None


DbSession = Annotated[Session, Depends(get_session)]
ClockDep = Annotated[Clock, Depends(get_clock)]
MemberId = Annotated[uuid.UUID, Depends(get_member_id)]
