"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

import json
import os
import re
import urllib.error
import urllib.request

SYSTEM_PROMPT = """You operate a browser to log in and change a password.
Return ONLY JSON with this shape:
{
  "goal": "login" | "change_password" | "done" | "blocked",
  "actions": [
    {"op": "fill"|"click"|"press"|"goto", "selector": "css selector", "value": "optional", "key": "Enter"}
  ],
  "reason": "short explanation",
  "error_code": null | "bot_protection" | "password_format" | "login_failed" | "unknown",
  "done": false
}

Rules:
- Use only selectors from the provided page snapshot.
- For login: fill username/email, fill current password, submit.
- After login, find account/security/password settings.
- For password change: fill current password, new password, confirm new password, submit.
- If Cloudflare or similar bot protection is present, set error_code=bot_protection and goal=blocked.
- If the page says the new password format is invalid, set error_code=password_format and goal=blocked.
- If credentials are rejected, set error_code=login_failed and goal=blocked.
- If the password was clearly changed, set done=true and goal=done.
- Prefer 1-3 actions per turn. Do not invent selectors.
"""


def _config_value(name, default=None):
    env = os.environ.get(name.upper()) or os.environ.get(name)
    if env:
        return env
    try:
        from yadacoin.core.config import Config
        cfg = Config()
        if hasattr(cfg, name):
            val = getattr(cfg, name)
            if val not in (None, ""):
                return val
    except Exception:
        pass
    return default


def llm_settings():
    return {
        "api_key": _config_value("openai_api_key") or _config_value("OPENAI_API_KEY"),
        "base_url": (
            _config_value("password_change_llm_base_url")
            or _config_value("openai_base_url")
            or "https://api.openai.com/v1"
        ).rstrip("/"),
        "model": _config_value("password_change_llm_model") or "gpt-4o-mini",
    }


def _parse_json_content(content):
    if not content:
        raise ValueError("empty LLM response")
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM JSON must be an object")
    data.setdefault("actions", [])
    data.setdefault("done", False)
    data.setdefault("error_code", None)
    data.setdefault("goal", "login")
    data.setdefault("reason", "")
    if not isinstance(data["actions"], list):
        data["actions"] = []
    return data


def decide_next_actions(snapshot, *, username, phase, last_reason=""):
    settings = llm_settings()
    if not settings["api_key"]:
        raise ValueError("openai_api_key is required for password-change discovery")
    payload = {
        "model": settings["model"],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "phase": phase,
                        "username": username,
                        "last_reason": last_reason,
                        "page": {
                            "url": snapshot.get("url"),
                            "title": snapshot.get("title"),
                            "text": (snapshot.get("text") or "")[:2500],
                            "elements": snapshot.get("elements") or [],
                            "error_code": snapshot.get("error_code"),
                        },
                        "secrets": {
                            "username_token": "{{username}}",
                            "password_token": "{{password}}",
                            "new_password_token": "{{new_password}}",
                        },
                        "instruction": (
                            "Choose selectors and actions. Use the secret tokens "
                            "exactly as {{username}}, {{password}}, {{new_password}} "
                            "in fill values. Never echo real secrets."
                        ),
                    }
                ),
            },
        ],
    }
    req = urllib.request.Request(
        f"{settings['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings['api_key']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"LLM HTTP {exc.code}: {detail}") from exc
    content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content"))
    return _parse_json_content(content)


def substitute_secrets(value, username, password, new_password):
    if value is None:
        return value
    text = str(value)
    return (
        text.replace("{{username}}", username)
        .replace("{{password}}", password)
        .replace("{{new_password}}", new_password)
    )
