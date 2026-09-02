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
