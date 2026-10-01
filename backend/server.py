from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import os  # noqa: E402
import logging  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
from core import client  # noqa: E402
import auth, crm, appointments, messaging, public, admin, cron, seed, billing, sales  # noqa: E402,E401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = FastAPI(title="RevLoop API")

for r in (auth.router, crm.router, appointments.router, messaging.router, public.router, admin.router, cron.router, billing.router, sales.router):
    app.include_router(r)

ORIGIN_EXEMPT = ("/api/webhooks/", "/api/cron/", "/api/stripe/webhook")


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.url.path.startswith(ORIGIN_EXEMPT):
        # CSRF defense: cross-site forms cannot set custom headers, and CORS only allows CORS_ORIGINS to preflight them.
        if request.headers.get("x-revloop-client") != "web" and not request.headers.get("authorization", "").startswith("Bearer "):
            return JSONResponse({"detail": "Request origin not allowed."}, status_code=403)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        field = ".".join(str(x) for x in e.get("loc", [])[1:])
        msgs.append(f"{field}: {e.get('msg')}" if field else e.get("msg"))
    return JSONResponse({"detail": "; ".join(msgs)}, status_code=422)


@app.get("/api/health")
async def health():
    await client.admin.command("ping")
    return {"ok": True}


app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await seed.run_seed()
    await billing.seed_plans()


@app.on_event("shutdown")
async def shutdown():
    client.close()
