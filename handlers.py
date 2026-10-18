"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.

Password-change automation plugin.

POST /password-change/jobs
  {url, username, password, new_password}
  Enqueues a Celery job that logs in via Playwright and changes the password.
  First run uses an LLM + LangGraph to discover the flow and persists it
  keyed by scheme://host:port. Later runs replay without an LLM. If the
  saved workflow breaks, rediscovery runs once.

GET /password-change/jobs/<job_id>
GET /password-change/workflows?url=...
DELETE /password-change/workflows?url=...
"""

import json

from yadacoin.http.base import BaseHandler

from plugins.passwordchange import store
from plugins.passwordchange.origin import origin_key


def enqueue_job(*, job_id, url, username, password, new_password):
    from plugins.passwordchange.tasks import enqueue_password_change

    return enqueue_password_change(
        job_id=job_id,
        url=url,
        username=username,
        password=password,
        new_password=new_password,
    )


def _json_body(handler):
    try:
        return json.loads(handler.request.body or b"{}")
    except Exception:
        return None


class PasswordChangeJobCreateHandler(BaseHandler):
    async def post(self):
        body = _json_body(self)
        if body is None:
            self.set_status(400)
            return self.render_as_json({"status": False, "message": "invalid json body"})

        url = (body.get("url") or "").strip()
        username = body.get("username") or ""
        password = body.get("password") or ""
        new_password = body.get("new_password") or ""
        if not url or not username or not password or not new_password:
            self.set_status(400)
            return self.render_as_json(
                {
                    "status": False,
                    "message": "url, username, password, and new_password are required",
                }
            )
        if password == new_password:
            self.set_status(400)
            return self.render_as_json(
                {"status": False, "message": "new_password must differ from password"}
            )
        try:
            origin = origin_key(url)
        except ValueError as exc:
            self.set_status(400)
            return self.render_as_json({"status": False, "message": str(exc)})

        job_id = store.new_job_id()
        job = await store.create_job(
            self.config.mongo,
            job_id=job_id,
            origin=origin,
            url=url,
            username=username,
        )
        try:
            async_result = enqueue_job(
                job_id=job_id,
                url=url,
                username=username,
                password=password,
                new_password=new_password,
            )
            job["celery_task_id"] = getattr(async_result, "id", None)
        except Exception as exc:
            await store.update_job(
                self.config.mongo,
                job_id,
                status="failed",
                phase="enqueue",
                error_code="unknown",
                error_message=f"failed to enqueue celery job: {exc}",
            )
            self.set_status(503)
            return self.render_as_json(
                {
                    "status": False,
                    "job_id": job_id,
                    "message": f"failed to enqueue celery job: {exc}",
                }
            )
        return self.render_as_json({"status": True, "job": job})


class PasswordChangeJobHandler(BaseHandler):
    async def get(self, job_id):
        job = await store.get_job(self.config.mongo, job_id)
        if not job:
            self.set_status(404)
            return self.render_as_json({"status": False, "message": "job not found"})
        return self.render_as_json({"status": True, "job": job})


class PasswordChangeWorkflowHandler(BaseHandler):
    async def get(self):
        url = (self.get_query_argument("url", "") or "").strip()
        origin = (self.get_query_argument("origin", "") or "").strip()
        if not origin:
            if not url:
                self.set_status(400)
                return self.render_as_json(
                    {"status": False, "message": "url or origin required"}
                )
            try:
                origin = origin_key(url)
            except ValueError as exc:
                self.set_status(400)
                return self.render_as_json({"status": False, "message": str(exc)})
        workflow = await store.get_workflow(self.config.mongo, origin)
        if not workflow:
            self.set_status(404)
            return self.render_as_json(
                {"status": False, "message": "no saved workflow", "origin": origin}
            )
        return self.render_as_json({"status": True, "workflow": workflow})

    async def delete(self):
        url = (self.get_query_argument("url", "") or "").strip()
        origin = (self.get_query_argument("origin", "") or "").strip()
        if not origin:
            if not url:
                self.set_status(400)
                return self.render_as_json(
                    {"status": False, "message": "url or origin required"}
                )
            try:
                origin = origin_key(url)
            except ValueError as exc:
                self.set_status(400)
                return self.render_as_json({"status": False, "message": str(exc)})
        await store.delete_workflow(self.config.mongo, origin)
        return self.render_as_json({"status": True, "origin": origin, "deleted": True})


HANDLERS = [
    (r"/password-change/jobs", PasswordChangeJobCreateHandler),
    (r"/password-change/jobs/([a-fA-F0-9]+)", PasswordChangeJobHandler),
    (r"/password-change/workflows", PasswordChangeWorkflowHandler),
]
