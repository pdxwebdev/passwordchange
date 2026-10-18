"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""


def route_after_load(workflow, error_code=None):
    if error_code:
        return "fail"
    return "replay" if workflow else "discover"


def route_after_replay(ok, allow_rediscover):
    """Replay a saved workflow; rediscover at most once if it is stale."""
    if ok:
        return "ok"
    if allow_rediscover:
        return "rediscover"
    return "fail"


def route_after_observe(error_code, done):
    if error_code:
        return "fail"
    if done:
        return "ok"
    return "decide"


def route_after_act(error_code, done, llm_turns, max_llm_turns):
    if error_code:
        return "fail"
    if done:
        return "ok"
    if llm_turns >= max_llm_turns:
        return "fail"
    return "observe"
