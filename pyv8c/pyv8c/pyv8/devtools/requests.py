"""Connection- and session-scoped CDP request correlation."""

from dataclasses import dataclass
from itertools import count
import threading


@dataclass
class Request:
    session: object
    original_id: int
    method: str


class Requests:
    def __init__(self):
        self.sequence = count(1)
        self.pending = {}
        self.lock = threading.RLock()

    def add(self, session, payload):
        with self.lock:
            identifier = next(self.sequence)
            self.pending[identifier] = Request(session, payload["id"], payload["method"])
        return {**payload, "id": identifier}

    def resolve(self, session, payload):
        with self.lock:
            request = self.pending.get(payload["id"])
            if request is None or request.session is not session:
                return None
            del self.pending[payload["id"]]
        payload["id"] = request.original_id
        return request

    def cancel(self, session):
        with self.lock:
            self.pending = {identifier: request for identifier, request in self.pending.items()
                            if request.session is not session}
