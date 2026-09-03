"""Shared descriptor exposing controller-owned state on the Page facade."""


class StateAlias:
    """Expose controller-owned state through the Page facade."""

    def __init__(self, component, attribute):
        self.component = component
        self.attribute = attribute

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return getattr(getattr(instance, self.component), self.attribute)

    def __set__(self, instance, value):
        setattr(getattr(instance, self.component), self.attribute, value)


class Ownership:
    """Track page-owned websockets, workers, and frames for teardown."""

    __slots__ = ("websockets", "workers", "frames")

    def __init__(self):
        self.websockets = set()
        self.workers = set()
        self.frames = set()

    def track(self, kind, obj):
        getattr(self, kind).add(obj)

    def discard(self, kind, obj):
        getattr(self, kind).discard(obj)

    def drain(self, kind):
        """Return the live set for ``kind`` and clear it."""
        bucket = getattr(self, kind)
        items = list(bucket)
        bucket.clear()
        return items
