"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

from plugins.passwordchange import store


async def go(app):
    mongo = getattr(getattr(app, "config", None), "mongo", None)
    if mongo is None:
        return
    try:
        await store.ensure_indexes(mongo)
    except Exception as exc:
        log = getattr(app.config, "app_log", None)
        if log:
            log.warning("passwordchange: failed to ensure indexes: %s", exc)
