"""Public, immutable debugging configuration."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DevToolsOptions:
    host: str = "127.0.0.1"
    port: int = 0
    wait_for_debugger: bool = False
    pause_on_start: bool = True
    startup_timeout: float | None = None

    def __post_init__(self):
        if not isinstance(self.host, str) or not self.host:
            raise ValueError("host must be a nonempty string")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        if type(self.wait_for_debugger) is not bool or type(self.pause_on_start) is not bool:
            raise TypeError("debugger flags must be bool")
        if self.startup_timeout is not None and (
            not math.isfinite(self.startup_timeout) or self.startup_timeout < 0
        ):
            raise ValueError("startup_timeout must be finite and nonnegative")
