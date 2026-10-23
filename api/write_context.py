"""Authenticated write identity propagated to document lifecycle hooks.

Set only at server dispatch boundaries, never from document/model arguments.
ContextVar isolates concurrent requests and is copied by asyncio.to_thread.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_ACTOR = ContextVar("document_write_actor", default=None)


def current_write_actor():
    return _ACTOR.get()


@contextmanager
def write_actor(user):
    token = _ACTOR.set(user)
    try:
        yield
    finally:
        _ACTOR.reset(token)


def run_as_actor(user, handler, *args):
    with write_actor(user):
        return handler(*args)
