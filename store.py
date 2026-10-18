"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

import time
import uuid


JOBS = "password_change_jobs"
WORKFLOWS = "password_change_workflows"


def _now():
    return time.time()


def new_job_id():
    return uuid.uuid4().hex


def site_db(mongo):
    return mongo.async_site_db if hasattr(mongo, "async_site_db") else mongo.site_db


def sync_site_db(mongo):
    if hasattr(mongo, "site_db") and mongo.site_db is not None:
        return mongo.site_db
    return mongo.async_site_db


async def ensure_indexes(mongo):
    db = site_db(mongo)
    await db[JOBS].create_index("job_id", unique=True)
    await db[JOBS].create_index([("status", 1), ("created_at", -1)])
    await db[JOBS].create_index("origin")
    await db[WORKFLOWS].create_index("origin", unique=True)


async def create_job(mongo, *, job_id, origin, url, username):
    doc = {
        "job_id": job_id,
        "origin": origin,
        "url": url,
        "username": username,
        "status": "queued",
        "phase": "queued",
        "error_code": None,
        "error_message": None,
        "error_detail": None,
        "rediscovery_used": False,
        "used_saved_workflow": False,
        "steps": [],
        "created_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
    }
    await site_db(mongo)[JOBS].insert_one(doc)
    return public_job(doc)


def public_job(doc):
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


async def get_job(mongo, job_id):
    return public_job(await site_db(mongo)[JOBS].find_one({"job_id": job_id}))


async def update_job(mongo, job_id, **fields):
    fields["updated_at"] = _now()
    if fields.get("status") in ("succeeded", "failed"):
        fields.setdefault("finished_at", _now())
    await site_db(mongo)[JOBS].update_one({"job_id": job_id}, {"$set": fields})
    return await get_job(mongo, job_id)


async def append_job_step(mongo, job_id, step):
    step = dict(step)
    step["at"] = _now()
    await site_db(mongo)[JOBS].update_one(
        {"job_id": job_id},
        {"$push": {"steps": step}, "$set": {"updated_at": _now()}},
    )


async def get_workflow(mongo, origin):
    doc = await site_db(mongo)[WORKFLOWS].find_one({"origin": origin})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc


async def save_workflow(mongo, origin, steps, start_url):
    now = _now()
    doc = {
        "origin": origin,
        "start_url": start_url,
        "steps": list(steps or []),
        "updated_at": now,
    }
    await site_db(mongo)[WORKFLOWS].update_one(
        {"origin": origin},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return doc


async def delete_workflow(mongo, origin):
    await site_db(mongo)[WORKFLOWS].delete_one({"origin": origin})


def sync_get_workflow(mongo, origin):
    db = sync_site_db(mongo)
    doc = db[WORKFLOWS].find_one({"origin": origin})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc


def sync_save_workflow(mongo, origin, steps, start_url):
    now = _now()
    doc = {
        "origin": origin,
        "start_url": start_url,
        "steps": list(steps or []),
        "updated_at": now,
    }
    db = sync_site_db(mongo)
    db[WORKFLOWS].update_one(
        {"origin": origin},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return doc


def sync_delete_workflow(mongo, origin):
    sync_site_db(mongo)[WORKFLOWS].delete_one({"origin": origin})


def sync_create_job(mongo, *, job_id, origin, url, username):
    doc = {
        "job_id": job_id,
        "origin": origin,
        "url": url,
        "username": username,
        "status": "queued",
        "phase": "queued",
        "error_code": None,
        "error_message": None,
        "error_detail": None,
        "rediscovery_used": False,
        "used_saved_workflow": False,
        "steps": [],
        "created_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
    }
    sync_site_db(mongo)[JOBS].insert_one(doc)
    return public_job(doc)


def sync_get_job(mongo, job_id):
    return public_job(sync_site_db(mongo)[JOBS].find_one({"job_id": job_id}))


def sync_update_job(mongo, job_id, **fields):
    fields["updated_at"] = _now()
    if fields.get("status") in ("succeeded", "failed"):
        fields.setdefault("finished_at", _now())
    sync_site_db(mongo)[JOBS].update_one({"job_id": job_id}, {"$set": fields})
    return sync_get_job(mongo, job_id)


def sync_append_job_step(mongo, job_id, step):
    step = dict(step)
    step["at"] = _now()
    sync_site_db(mongo)[JOBS].update_one(
        {"job_id": job_id},
        {"$push": {"steps": step}, "$set": {"updated_at": _now()}},
    )
