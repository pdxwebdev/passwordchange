"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

from plugins.passwordchange import failures, policy
from plugins.passwordchange.browser import looks_like_success
from plugins.passwordchange.llm import decide_next_actions, substitute_secrets


MAX_LLM_TURNS = 18
WORKFLOW_VERSION = 1


def compile_graph():
    """Build the LangGraph StateGraph used for discover/replay."""
    from langgraph.graph import END, StateGraph

    graph = StateGraph(dict)
    graph.add_node("load", node_load)
    graph.add_node("replay", node_replay)
    graph.add_node("observe", node_observe)
    graph.add_node("decide", node_decide)
    graph.add_node("act", node_act)
    graph.add_node("ok", node_ok)
    graph.add_node("fail", node_fail)

    graph.set_entry_point("load")
    graph.add_conditional_edges(
        "load",
        lambda s: policy.route_after_load(s.get("workflow"), s.get("error_code")),
        {"replay": "replay", "discover": "observe", "fail": "fail"},
    )
    graph.add_conditional_edges(
        "replay",
        lambda s: policy.route_after_replay(s.get("ok"), s.get("allow_rediscover")),
        {"ok": "ok", "rediscover": "observe", "fail": "fail"},
    )
    graph.add_conditional_edges(
        "observe",
        lambda s: policy.route_after_observe(s.get("error_code"), s.get("done")),
        {"fail": "fail", "ok": "ok", "decide": "decide"},
    )
    graph.add_edge("decide", "act")
    graph.add_conditional_edges(
        "act",
        lambda s: policy.route_after_act(
            s.get("error_code"),
            s.get("done"),
            s.get("llm_turns") or 0,
            s.get("max_llm_turns") or MAX_LLM_TURNS,
        ),
        {"fail": "fail", "ok": "ok", "observe": "observe"},
    )
    graph.add_edge("ok", END)
    graph.add_edge("fail", END)
    return graph.compile()


def initial_state(*, url, origin, username, password, new_password, workflow=None, session=None, store=None, job_id=None):
    return {
        "url": url,
        "origin": origin,
        "username": username,
        "password": password,
        "new_password": new_password,
        "workflow": workflow,
        "session": session,
        "store": store,
        "job_id": job_id,
        "snapshot": None,
        "recorded_steps": [],
        "pending_actions": [],
        "last_reason": "",
        "phase": "start",
        "llm_turns": 0,
        "max_llm_turns": MAX_LLM_TURNS,
        "ok": False,
        "done": False,
        "error_code": None,
        "error_message": None,
        "error_detail": None,
        "rediscovery_used": False,
        "allow_rediscover": False,
        "used_saved_workflow": bool(workflow),
        "result": None,
    }


async def node_load(state):
    session = state["session"]
    start = (state.get("workflow") or {}).get("start_url") or state["url"]
    snapshot = await session.goto(start)
    snapshot = _annotate_snapshot(snapshot)
    state["snapshot"] = snapshot
    state["phase"] = "loaded"
    if snapshot.get("error_code") == failures.BOT_PROTECTION:
        state["error_code"] = failures.BOT_PROTECTION
        state["error_message"] = "Cloudflare or similar bot protection is blocking the browser"
        state["error_detail"] = snapshot.get("title")
    _log(state, "load", start)
    return state


async def node_replay(state):
    session = state["session"]
    workflow = state.get("workflow") or {}
    steps = list(workflow.get("steps") or [])
    if not steps:
        state["ok"] = False
        state["error_code"] = failures.WORKFLOW_STALE
        state["error_message"] = "saved workflow has no steps"
        return state
    try:
        snapshot = state.get("snapshot")
        for step in steps:
            snapshot = _annotate_snapshot(await _apply_step(session, step, state))
            code = (snapshot or {}).get("error_code")
            if code in (failures.BOT_PROTECTION, failures.PASSWORD_FORMAT, failures.LOGIN_FAILED):
                state["snapshot"] = snapshot
                state["ok"] = False
                state["error_code"] = code
                state["error_message"] = _default_message(code)
                state["error_detail"] = (snapshot or {}).get("title")
                return state
        state["snapshot"] = snapshot
        if looks_like_success(snapshot, state["new_password"]):
            state["ok"] = True
            state["done"] = True
            state["phase"] = "replayed"
            return state
        state["ok"] = False
        state["error_code"] = failures.WORKFLOW_STALE
        state["error_message"] = "saved workflow did not change the password"
        if not state.get("rediscovery_used"):
            _mark_rediscover(state, "saved workflow stale; rediscovering once")
        return state
    except Exception as exc:
        state["ok"] = False
        code = failures.classify_exception(exc)
        if code == failures.WORKFLOW_STALE and not state.get("rediscovery_used"):
            _mark_rediscover(state, str(exc))
            return state
        state["error_code"] = code
        state["error_message"] = str(exc)
        return state


async def node_observe(state):
    if state.get("allow_rediscover"):
        state["rediscovery_used"] = True
        state["allow_rediscover"] = False
    session = state["session"]
    snapshot = _annotate_snapshot(state.get("snapshot") or await session.snapshot())
    state["snapshot"] = snapshot
    code = snapshot.get("error_code")
    if code in (failures.BOT_PROTECTION, failures.PASSWORD_FORMAT, failures.LOGIN_FAILED):
        state["error_code"] = code
        state["error_message"] = _default_message(code)
        state["error_detail"] = snapshot.get("title")
        return state
    if looks_like_success(snapshot, state["new_password"]) and state.get("recorded_steps"):
        state["done"] = True
        state["ok"] = True
    state["phase"] = "observe"
    return state


async def node_decide(state):
    try:
        decision = decide_next_actions(
            state.get("snapshot") or {},
            username=state["username"],
            phase=state.get("phase") or "discover",
            last_reason=state.get("last_reason") or "",
        )
    except Exception as exc:
        state["error_code"] = failures.LLM_ERROR
        state["error_message"] = str(exc)
        return state
    state["llm_turns"] = int(state.get("llm_turns") or 0) + 1
    state["last_reason"] = decision.get("reason") or ""
    state["pending_actions"] = list(decision.get("actions") or [])
    if decision.get("error_code"):
        state["error_code"] = decision["error_code"]
        state["error_message"] = decision.get("reason") or _default_message(decision["error_code"])
        return state
    if decision.get("done") or decision.get("goal") == "done":
        state["done"] = True
        state["ok"] = True
        return state
    if not state["pending_actions"]:
        if state.get("llm_turns", 0) >= state.get("max_llm_turns", MAX_LLM_TURNS):
            state["error_code"] = failures.REDISCOVERY_FAILED if state.get("rediscovery_used") else failures.UNKNOWN
            state["error_message"] = "LLM returned no actions"
        else:
            state["last_reason"] = "empty action list"
    _log(state, "decide", state.get("last_reason"))
    return state


async def node_act(state):
    session = state["session"]
    actions = list(state.get("pending_actions") or [])
    state["pending_actions"] = []
    snapshot = state.get("snapshot")
    try:
        for action in actions:
            recorded, snapshot = await _run_action(session, action, state)
            snapshot = _annotate_snapshot(snapshot)
            if recorded:
                state["recorded_steps"].append(recorded)
            code = (snapshot or {}).get("error_code")
            if code in (failures.BOT_PROTECTION, failures.PASSWORD_FORMAT, failures.LOGIN_FAILED):
                state["snapshot"] = snapshot
                state["error_code"] = code
                state["error_message"] = _default_message(code)
                state["error_detail"] = (snapshot or {}).get("title")
                return state
        state["snapshot"] = snapshot
        if looks_like_success(snapshot, state["new_password"]) and state.get("recorded_steps"):
            state["done"] = True
            state["ok"] = True
        if state.get("llm_turns", 0) >= state.get("max_llm_turns", MAX_LLM_TURNS) and not state.get("done"):
            state["error_code"] = (
                failures.REDISCOVERY_FAILED if state.get("rediscovery_used") else failures.UNKNOWN
            )
            state["error_message"] = "exceeded LLM turn budget without changing the password"
        return state
    except Exception as exc:
        state["error_code"] = failures.classify_exception(exc)
        state["error_message"] = str(exc)
        return state


async def node_ok(state):
    steps = list(state.get("recorded_steps") or [])
    if not steps and state.get("workflow"):
        steps = list((state.get("workflow") or {}).get("steps") or [])
    if steps:
        _persist_workflow(state, steps)
    state["ok"] = True
    state["done"] = True
    state["phase"] = "succeeded"
    state["result"] = {
        "status": "succeeded",
        "origin": state["origin"],
        "used_saved_workflow": bool(state.get("used_saved_workflow")),
        "rediscovery_used": bool(state.get("rediscovery_used")),
        "steps": steps,
    }
    _log(state, "ok", f"{len(steps)} steps saved for {state['origin']}")
    return state


async def node_fail(state):
    code = state.get("error_code") or failures.UNKNOWN
    if state.get("rediscovery_used") and code == failures.WORKFLOW_STALE:
        code = failures.REDISCOVERY_FAILED
        state["error_code"] = code
        state["error_message"] = state.get("error_message") or "rediscovery failed after saved workflow broke"
    state["ok"] = False
    state["phase"] = "failed"
    state["result"] = failures.failure_payload(
        code,
        state.get("error_message") or _default_message(code),
        state.get("error_detail"),
        rediscovery_used=state.get("rediscovery_used"),
    )
    _log(state, "fail", state["result"]["error_message"])
    return state


async def _run_action(session, action, state):
    op = (action or {}).get("op") or (action or {}).get("type")
    selector = (action or {}).get("selector") or ""
    raw_value = (action or {}).get("value")
    value = substitute_secrets(
        raw_value, state["username"], state["password"], state["new_password"]
    )
    if op == "goto":
        target = value or selector
        snapshot = await session.goto(target)
        return {"op": "goto", "url": target}, snapshot
    if op == "fill":
        snapshot = await session.fill(selector, value)
        secret = _secret_token(raw_value)
        return {"op": "fill", "selector": selector, "value": secret or "{{password}}"}, snapshot
    if op == "click":
        snapshot = await session.click(selector)
        return {"op": "click", "selector": selector}, snapshot
    if op == "press":
        key = (action or {}).get("key") or "Enter"
        snapshot = await session.press(selector, key)
        return {"op": "press", "selector": selector, "key": key}, snapshot
    raise ValueError(f"unsupported action op: {op}")


async def _apply_step(session, step, state):
    op = step.get("op")
    if op == "goto":
        return await session.goto(step.get("url") or state["url"])
    if op == "fill":
        value = substitute_secrets(
            step.get("value"), state["username"], state["password"], state["new_password"]
        )
        return await session.fill(step.get("selector"), value)
    if op == "click":
        return await session.click(step.get("selector"))
    if op == "press":
        return await session.press(step.get("selector"), step.get("key") or "Enter")
    raise ValueError(f"unsupported workflow step: {op}")


def _annotate_snapshot(snapshot):
    snapshot = dict(snapshot or {})
    if not snapshot.get("error_code"):
        snapshot["error_code"] = failures.classify_page_text(
            snapshot.get("title"), snapshot.get("text"), snapshot.get("url")
        )
    return snapshot


def _mark_rediscover(state, message):
    state["workflow"] = None
    state["used_saved_workflow"] = False
    state["allow_rediscover"] = True
    state["recorded_steps"] = []
    state["pending_actions"] = []
    state["phase"] = "rediscover"
    state["error_code"] = None
    state["error_message"] = None
    state["ok"] = False
    _log(state, "rediscover", message)


def _secret_token(raw_value):
    text = str(raw_value or "")
    for token in ("{{username}}", "{{password}}", "{{new_password}}"):
        if token in text:
            return token
    return None


def _default_message(code):
    return {
        failures.BOT_PROTECTION: "Cloudflare or similar bot protection is blocking the browser",
        failures.PASSWORD_FORMAT: "the site rejected the new password format",
        failures.LOGIN_FAILED: "login failed with the provided credentials",
        failures.WORKFLOW_STALE: "saved workflow no longer matches the site",
        failures.TIMEOUT: "timed out waiting for the page",
        failures.NAVIGATION: "navigation failed",
        failures.LLM_ERROR: "LLM decision failed",
        failures.REDISCOVERY_FAILED: "rediscovery failed after the saved workflow broke",
        failures.UNKNOWN: "password change failed",
    }.get(code, "password change failed")


def _persist_workflow(state, steps):
    store = state.get("store")
    origin = state.get("origin")
    if not store or not origin:
        return
    payload_steps = [
        {k: v for k, v in step.items() if k != "at"}
        for step in steps
    ]
    start_url = state.get("url")
    if hasattr(store, "save_workflow_sync"):
        store.save_workflow_sync(origin, payload_steps, start_url)
    elif callable(store):
        store(origin, payload_steps, start_url)


def _log(state, phase, message):
    store = state.get("store")
    job_id = state.get("job_id")
    if not store or not job_id:
        return
    step = {"phase": phase, "message": str(message or "")[:500]}
    if hasattr(store, "append_step"):
        store.append_step(job_id, step)
