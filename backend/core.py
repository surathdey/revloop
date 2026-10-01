import os
import json
import uuid
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from fastapi import HTTPException
from cryptography.fernet import Fernet

client = AsyncIOMotorClient(os.environ["MONGO_URL"], tz_aware=True)
db = client[os.environ["DB_NAME"]]
_fernet = Fernet(os.environ["SETTINGS_ENCRYPTION_KEY"].encode())

# key -> is_secret. Values live encrypted in platform_settings and take effect without redeploy.
SETTING_KEYS = {
    "TWILIO_ACCOUNT_SID": True,
    "TWILIO_AUTH_TOKEN": True,
    "TWILIO_MESSAGING_SERVICE_SID": False,
    "SMS_MODE": False,
    "BILLING_GRACE_DAYS": False,
    "VAPI_API_KEY": True,
    "VAPI_PHONE_NUMBER_ID": False,
    "VAPI_WEBHOOK_SECRET": True,
    "ELEVENLABS_API_KEY": True,
    "ELEVENLABS_VOICE_ID": False,
}


def now():
    return datetime.now(timezone.utc)


def new_id():
    return uuid.uuid4().hex


def new_token():
    return secrets.token_urlsafe(24)


def sha(s: str):
    return hashlib.sha256(s.encode()).hexdigest()


def origin():
    return os.environ["PUBLIC_BASE_URL"].rstrip("/")


class AppError(HTTPException):
    def __init__(self, message: str, status: int = 400):
        super().__init__(status_code=status, detail=message)


async def audit(tenant_id, actor, action, entity_id, detail=None):
    await db.audits.insert_one({
        "id": new_id(), "tenant_id": tenant_id, "actor": actor, "action": action,
        "entity_id": entity_id, "detail": json.dumps(detail or {}, default=str), "created_at": now(),
    })


async def rate_limit(key: str, max_count: int, window_s: int):
    t = now()
    await db.rate_limits.delete_many({"key": key, "reset_at": {"$lt": t}})
    row = await db.rate_limits.find_one_and_update(
        {"key": key},
        {"$inc": {"count": 1}, "$setOnInsert": {"reset_at": t + timedelta(seconds=window_s)}},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    if row["count"] > max_count:
        raise AppError("Too many requests. Please try again later.", 429)


async def platform_config() -> dict:
    out = {"SMS_MODE": "simulated"}
    async for d in db.platform_settings.find({}, {"_id": 0}):
        out[d["key"]] = _fernet.decrypt(d["ciphertext"].encode()).decode()
    return out


async def save_setting(key: str, value: str):
    if not value:
        await db.platform_settings.delete_one({"key": key})
        return
    await db.platform_settings.update_one(
        {"key": key},
        {"$set": {"ciphertext": _fernet.encrypt(value.encode()).decode(), "updated_at": now()}},
        upsert=True,
    )


class Scoped:
    """Tenant-scoped collection access: every query is forced to carry tenant_id."""

    def __init__(self, tenant_id: str):
        self.tid = tenant_id

    def f(self, q=None):
        return {**(q or {}), "tenant_id": self.tid}

    async def find(self, coll, q=None, sort=None, limit=0, proj=None):
        cur = db[coll].find(self.f(q), {"_id": 0, **(proj or {})})
        if sort:
            cur = cur.sort(sort)
        if limit:
            cur = cur.limit(limit)
        return await cur.to_list(limit or 20000)

    async def one(self, coll, q):
        return await db[coll].find_one(self.f(q), {"_id": 0})

    async def get(self, coll, id_, msg="Not found."):
        d = await self.one(coll, {"id": id_})
        if not d:
            raise AppError(msg, 404)
        return d

    async def insert(self, coll, doc):
        doc = {"id": new_id(), "created_at": now(), **doc, "tenant_id": self.tid}
        await db[coll].insert_one(doc)
        doc.pop("_id", None)
        return doc

    async def update(self, coll, q, upd, many=False):
        fn = db[coll].update_many if many else db[coll].update_one
        return await fn(self.f(q), upd)

    async def count(self, coll, q=None):
        return await db[coll].count_documents(self.f(q))
