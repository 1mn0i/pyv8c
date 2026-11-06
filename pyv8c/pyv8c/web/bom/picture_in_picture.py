"""``documentPictureInPicture``: the handle Chrome exposes before a window opens."""

from pyv8c import pyv8

from ...idl import interface
from ..dom.events import EventTarget


@interface('DocumentPictureInPicture', 'EventTarget')
class DocumentPictureInPicture(EventTarget):
    """The page-side handle for the picture-in-picture window."""

    def get_window(self):
        """No window is open until ``requestWindow`` succeeds, so this stays null."""
        return pyv8.Null

    def get_onenter(self):
        return self._get_handler("onenter")

    def set_onenter(self, value):
        self._set_handler("onenter", value)
