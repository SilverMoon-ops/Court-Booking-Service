from fastapi import FastAPI

app = FastAPI(title="Court Booking Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
