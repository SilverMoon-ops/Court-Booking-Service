"""Errors that become clean HTTP responses: {"error": {"code": ..., "message": ...}}."""


class AppError(Exception):
    status_code: int = 400
    code: str = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MemberRequired(AppError):
    status_code = 401
    code = "member_required"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class SlotUnavailable(AppError):
    status_code = 409
    code = "slot_unavailable"


class BookingLimitReached(AppError):
    status_code = 409
    code = "booking_limit_reached"


class HoldExpired(AppError):
    status_code = 410
    code = "hold_expired"


class InvalidState(AppError):
    status_code = 409
    code = "invalid_state"


class StaffRequired(AppError):
    status_code = 401
    code = "staff_required"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class StaffNotConfigured(AppError):
    status_code = 503
    code = "staff_not_configured"
