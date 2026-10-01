import os
import hmac
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError
from core import db, now
from messaging import process_queue, scan_due
from sales import run_campaigns

router = APIRouter(prefix="/api/cron")


def _authorized(request: Request) -> bool:
    h = request.headers.get("Authorization", "")
    return h.startswith("Bearer ") and hmac.compare_digest(h[7:], os.environ["WEBHOOK_CRON_SECRET"])


async def _accept(request: Request, name: str):
    run_id = request.headers.get("X-Webhook-Id")
    if not run_id:
        try:
            run_id = (await request.json()).get("run_id")
        except Exception:
            return None
    if not run_id:
        return None
    try:
        await db.cron_runs.insert_one({"id": f"{name}:{run_id}", "name": name, "created_at": now()})
        return True
    except DuplicateKeyError:
        return False


async def _tick():
    await process_queue()
    await run_campaigns()


async def _nightly():
    await scan_due()
    await process_queue()


@router.post("/tick")
async def tick(request: Request, bg: BackgroundTasks):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    ok = await _accept(request, "tick")
    if ok is None:
        return JSONResponse({"detail": "Invalid body"}, status_code=400)
    if ok:
        bg.add_task(_tick)
    return {"accepted": True, "duplicate": not ok}


@router.post("/nightly")
async def nightly(request: Request, bg: BackgroundTasks):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    ok = await _accept(request, "nightly")
    if ok is None:
        return JSONResponse({"detail": "Invalid body"}, status_code=400)
    if ok:
        bg.add_task(_nightly)
    return {"accepted": True, "duplicate": not ok}
