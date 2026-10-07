"""Turning engine work into promises, for whichever realm the work belongs to.

Three functions and one rule: a promise is built by ``v8::Promise::Resolver`` through
``pyv8.PromiseResolver`` -- never by evaluating JavaScript -- and a realm that has closed
answers instead of raising.  That rule used to live in a per-realm ``RealmScripts`` object,
which existed for the JavaScript helpers it cached; with the native resolver in place there is
nothing to cache, so the policy is stateless and the realm keeps only its own ``context``.

A closed realm is a real case, not a defensive one: replacing a document closes the old realm
while work queued by the old document is still delivered (see ``Page._renew_document_realm`` and
``navigation`` cancelling only timers).  Settling a promise nobody can read is not an error the
page should ever see, and the native resolver's own error -- "the realm this PromiseResolver
belongs to is closed" -- is kept for the calls that ask for one directly.
"""

from __future__ import annotations

import pyv8


def _no_op(*_args, **_kwargs):
    """Settle a promise nobody can observe: say nothing, change nothing."""


def make_promise(context):
    """A pending promise and the two callables that settle it, for *context*'s realm.

    The settle functions hold the native resolver alive until they are called or dropped, so a
    fetch, a timer or a worker reply can settle whenever it finishes.
    """
    if context is None:
        return None, _no_op, _no_op
    resolver = pyv8.PromiseResolver(context)
    return resolver.promise(), resolver.resolve, resolver.reject


def promise_resolve(context, value):
    """*value* as a resolved promise, or *value* itself once the realm is gone."""
    if context is None:
        return value
    resolver = pyv8.PromiseResolver(context)
    resolver.resolve(value)
    return resolver.promise()


def enqueue_microtask(context, callback):
    """Queue *callback* on the realm's own microtask queue, or drop it if the realm is gone."""
    if context is None:
        return False
    return context.enqueue_microtask(callback)


def js_error_value(exc):
    """What a rejection should carry for *exc*.

    A Python exception crosses into JavaScript when the resolver settles it, so nothing has to
    be built here; anything that is not an exception (a JavaScript value, a string) is a
    legitimate rejection reason and crosses as itself, which is what the page would see from a
    native rejection too.
    """
    if isinstance(exc, pyv8.JSException):
        return exc.original
    return exc


__all__ = ["make_promise", "promise_resolve", "enqueue_microtask", "js_error_value"]
