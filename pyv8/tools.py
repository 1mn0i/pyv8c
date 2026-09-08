from loguru import logger

from .flag import *
from . import _pyv8

__hook_exclude_name = (
    "dir", "dirxml", "profile", "profileEnd", "clear", "table", "keys", "values",
    "debug", "undebug", "inspect", "copy", "queryObjects", 'monitor', 'unmonitor',
    "$", "$$", "$_", "$0", "$1", "$2", "$3", "$4", "$x", "DataView",
    "String", "Array", "Date", "Object", "window", "Symbol", "Number", "Function",
    "Symbol.toPrimitive", "Symbol.toStringTag",
    "parseFloat", "parseInt", "Math", "BigInt", "RegExp", "console", "isNaN", "Boolean",
    "faker",  "unescape", "NaN", "Infinity", "decodeURIComponent"
)


def __v8_get_hook__(self, name, value=None, error=None, ctx_type=''):
    if name in __hook_exclude_name or isinstance(name, str) and name.startswith('_'):
        return
    msg = f"{ctx_type} getter: {self.__class__.__name__}.{name} -> {value}"
    if error:
        msg += f", error: {error}"
    logger.info(msg)


def __v8_set_hook__(self, name, value, error, ctx_type=''):
    if isinstance(name, str) and name.startswith('_'):
        return
    msg = f"{ctx_type} setter: {self.__class__.__name__}.{name} = {value}"
    if error:
        msg += f", error: {error}"
    logger.info(msg)


def __v8_method_hook__(self, func_name, arguments, value, has_error, ctx_type=''):
    msg = f"{ctx_type} method: {self.__name__ if isinstance(self, type) else self.__class__.__name__}.{func_name}{tuple(arguments)}"
    if has_error:
        msg += f" -> error: {value}"
    else:
        msg += f" -> {value}"
    logger.info(msg)


def __v8_construct_hook__(name, arguments, value, has_error, is_construct_call, ctx_type=''):
    if is_construct_call:
        msg = f"{ctx_type} construct: new {name}{tuple(arguments)} -> {value}"
    else:
        msg = f"{ctx_type} construct: {name}{tuple(arguments)} -> {value}"
    if has_error:
        msg += f", has_error: {has_error}"
    logger.info(msg)



__all__ = [
    "__v8_construct_hook__", "__v8_get_hook__",
    "__v8_method_hook__", "__v8_set_hook__",
]
