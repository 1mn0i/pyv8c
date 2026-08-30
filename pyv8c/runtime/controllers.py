"""State owners used by the Page orchestration facade."""

import itertools
from collections import deque


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


class NavigationController:
    def __init__(self):
        self.generation = 0
        self.has_navigation = False
        self.pending = 0
        self.parser = None
        self.parser_generation = 0
        self.parsing = False
        self.parser_done = False


class DocumentLifecycle:
    def __init__(self):
        self.dom_content_loaded = False
        self.document_load_fired = False
        self.load_waiters = []


class ScriptController:
    def __init__(self):
        self.dom_blocking_scripts = 0
        self.queue = deque()
        self.defer = deque()
        self.module_cache = {}

    def reset(self):
        self.dom_blocking_scripts = 0
        self.queue.clear()
        self.defer.clear()
        self.module_cache.clear()


class ResourceController:
    def __init__(self):
        self.resources = {}
        self.counter = itertools.count(1)

    def reset(self):
        self.resources.clear()
