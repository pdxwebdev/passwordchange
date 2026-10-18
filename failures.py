"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

BOT_PROTECTION = "bot_protection"
PASSWORD_FORMAT = "password_format"
LOGIN_FAILED = "login_failed"
WORKFLOW_STALE = "workflow_stale"
TIMEOUT = "timeout"
NAVIGATION = "navigation"
LLM_ERROR = "llm_error"
REDISCOVERY_FAILED = "rediscovery_failed"
UNKNOWN = "unknown"

BOT_HINTS = (
    "cloudflare",
    "attention required",
    "checking your browser",
    "cf-browser-verification",
    "just a moment...",
    "enable javascript and cookies",
    "hcaptcha",
    "recaptcha",
    "datadome",
    "perimeterx",
    "px-captcha",
    "access denied",
    "bot detection",
    "unusual traffic",
    "verify you are human",
    "please verify you are a human",
    "why have i been blocked",
    "sorry, you have been blocked",
    "challenge-platform",
    "cf-chl-",
    "akamai",
    "imperva",
    "incapsula",
)

PASSWORD_FORMAT_HINTS = (
    "password must",
    "password should",
    "password is too short",
    "password too short",
    "password is too weak",
    "too weak",
    "does not meet",
    "doesn't meet",
    "password requirements",
    "password requirement",
    "special character",
    "at least 8 character",
    "at least 10 character",
    "at least 12 character",
    "minimum of 8",
    "minimum of 10",
    "one uppercase",
    "one lowercase",
    "one number",
    "one digit",
    "cannot be the same as",
    "must not match",
    "previously used password",
    "common password",
    "choose a stronger",
    "invalid password format",
    "password does not match the requirements",
)

LOGIN_FAILED_HINTS = (
    "invalid password",
    "incorrect password",
    "wrong password",
    "invalid credentials",
    "invalid email or password",
    "invalid username or password",
    "authentication failed",
    "login failed",
    "sign-in failed",
    "signin failed",
    "we don't recognize",
    "we do not recognize",
    "account not found",
    "user not found",
    "no account found",
    "couldn't find your",
    "could not find your",
    "email or password is incorrect",
    "username or password is incorrect",
)


def _haystack(*parts):
    return " ".join(p or "" for p in parts).lower()


def classify_page_text(*parts):
    text = _haystack(*parts)
    if not text.strip():
        return None
    if any(h in text for h in BOT_HINTS):
        return BOT_PROTECTION
    if any(h in text for h in PASSWORD_FORMAT_HINTS):
        return PASSWORD_FORMAT
    if any(h in text for h in LOGIN_FAILED_HINTS):
        return LOGIN_FAILED
    return None


def classify_exception(exc):
    name = type(exc).__name__.lower()
    msg = str(exc or "").lower()
    combined = f"{name} {msg}"
    if "timeout" in combined:
        return TIMEOUT
    if "net::" in combined or "navigation" in combined or "ns_error" in combined:
        return NAVIGATION
    if any(h in combined for h in BOT_HINTS):
        return BOT_PROTECTION
    if "selector" in combined or "strict mode" in combined or "not found" in combined:
        return WORKFLOW_STALE
    return UNKNOWN


def failure_payload(code, message, detail=None, rediscovery_used=False):
    return {
        "error_code": code or UNKNOWN,
        "error_message": message or "job failed",
        "error_detail": detail,
        "rediscovery_used": bool(rediscovery_used),
    }
