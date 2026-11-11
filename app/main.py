from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import AppError
from app.policies import InvalidSlot, InvalidWeek
from app.routers import bookings, courts, holds, recurring, staff

app = FastAPI(title="Court Booking Service")
app.include_router(holds.router)
app.include_router(bookings.router)
app.include_router(courts.router)
app.include_router(recurring.router)
app.include_router(staff.router)


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))


@app.exception_handler(InvalidSlot)
async def handle_invalid_slot(request: Request, exc: InvalidSlot) -> JSONResponse:
    return JSONResponse(status_code=422, content=error_body("invalid_slot", str(exc)))


@app.exception_handler(InvalidWeek)
async def handle_invalid_week(request: Request, exc: InvalidWeek) -> JSONResponse:
    return JSONResponse(status_code=422, content=error_body("invalid_week", str(exc)))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
