"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

import asyncio
import logging

from plugins.passwordchange import failures, store
from plugins.passwordchange.graph import compile_graph, initial_state
from plugins.passwordchange.origin import origin_key


def _celery_app():
    from plugins.passwordchange.celery_app import celery_app

    if celery_app is None:
        raise RuntimeError("celery is not available")
    return celery_app

LOG = logging.getLogger("passwordchange")


class JobStoreAdapter:
    def __init__(self, mongo):
        self.mongo = mongo

    def save_workflow_sync(self, origin, steps, start_url):
        store.sync_save_workflow(self.mongo, origin, steps, start_url)

    def append_step(self, job_id, step):
        store.sync_append_job_step(self.mongo, job_id, step)


def _mongo():
    from yadacoin.core.config import Config
    from yadacoin.core.mongo import Mongo

    cfg = Config()
    if getattr(cfg, "mongo", None) is None:
        cfg.mongo = Mongo()
    return cfg.mongo


def _bind_task():
    app = _celery_app()
    return app.task(bind=True, name="passwordchange.change_password")(change_password_job)


def enqueue_password_change(*, job_id, url, username, password, new_password):
    task = getattr(change_password_job, "delay", None)
    if task is None:
        bound = _bind_task()
        return bound.delay(job_id, url, username, password, new_password)
    return change_password_job.delay(job_id, url, username, password, new_password)


def change_password_job(self, job_id=None, url=None, username=None, password=None, new_password=None):
    if job_id is None and isinstance(self, str):
        job_id, url, username, password, new_password = (
            self,
            job_id,
            url,
            username,
            password,
        )
    mongo = _mongo()
    origin = origin_key(url)
    store.sync_update_job(mongo, job_id, status="running", phase="start", origin=origin)
    try:
        result = run_password_change(
            mongo,
            job_id=job_id,
            url=url,
            username=username,
            password=password,
            new_password=new_password,
        )
        if result.get("status") == "succeeded":
            store.sync_update_job(
                mongo,
                job_id,
                status="succeeded",
                phase="succeeded",
                used_saved_workflow=result.get("used_saved_workflow"),
                rediscovery_used=result.get("rediscovery_used"),
                error_code=None,
                error_message=None,
            )
        else:
            store.sync_update_job(
                mongo,
                job_id,
                status="failed",
                phase="failed",
                error_code=result.get("error_code"),
                error_message=result.get("error_message"),
                error_detail=result.get("error_detail"),
                rediscovery_used=result.get("rediscovery_used"),
            )
        return result
    except Exception as exc:
        LOG.exception("password change job %s crashed", job_id)
        payload = failures.failure_payload(
            failures.classify_exception(exc),
            str(exc),
        )
        store.sync_update_job(
            mongo,
            job_id,
            status="failed",
            phase="failed",
            error_code=payload["error_code"],
            error_message=payload["error_message"],
        )
        return payload


def run_password_change(mongo, *, job_id, url, username, password, new_password):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("event loop already running")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(
        arun_password_change(
            mongo,
            job_id=job_id,
            url=url,
            username=username,
            password=password,
            new_password=new_password,
        )
    )


async def arun_password_change(mongo, *, job_id, url, username, password, new_password, session=None):
    from plugins.passwordchange.browser import BrowserSession, launch_browser

    origin = origin_key(url)
    workflow = store.sync_get_workflow(mongo, origin)
    adapter = JobStoreAdapter(mongo)
    owns_browser = session is None
    browser = context = page = None
    try:
        if session is None:
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            browser, context, page = await launch_browser(pw)
            session = BrowserSession(page)
            session._pw = pw
        graph = compile_graph()
        state = initial_state(
            url=url,
            origin=origin,
            username=username,
            password=password,
            new_password=new_password,
            workflow=workflow,
            session=session,
            store=adapter,
            job_id=job_id,
        )
        final = await graph.ainvoke(state)
        return final.get("result") or failures.failure_payload(
            failures.UNKNOWN, "workflow ended without a result"
        )
    finally:
        if owns_browser:
            for closer in (page, context, browser):
                if closer is None:
                    continue
                try:
                    await closer.close()
                except Exception:
                    pass
            pw = getattr(session, "_pw", None) if session else None
            if pw is not None:
                try:
                    await pw.stop()
                except Exception:
                    pass


try:
    change_password_job = _bind_task()
except Exception:
    pass
