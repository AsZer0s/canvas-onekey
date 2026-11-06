"""Canvas Onekey: old ComfyUI + Python without os.memfd_create compatibility.

Only replaces the anonymous-file factory, never modifies os or upstream files.
"""
from __future__ import annotations

import ast
import ctypes
import inspect
import logging
import operator
import os
import sys
import textwrap

NODE_CLASS_MAPPINGS = {}


def needs_fallback(factory) -> bool:
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(factory)))
    except (OSError, TypeError, SyntaxError):
        return False
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
               and node.func.attr == "memfd_create" for node in ast.walk(tree))


def install(module, utils=None) -> bool:
    if sys.platform != "linux" or callable(getattr(os, "memfd_create", None)):
        return False
    original = getattr(module, "create_anonymous_file", None)
    if original is None or not needs_fallback(original):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        create = libc.memfd_create
    except (OSError, AttributeError):
        logging.warning("[canvas-onekey] libc has no memfd_create; upgrade the ComfyUI runtime/container.")
        return False
    create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    create.restype = ctypes.c_int

    def create_anonymous_file(size=0):
        size = operator.index(size)
        if not 0 <= size <= 0x7fffffffffffffff:
            raise ValueError("size must fit a nonnegative signed 64-bit file offset")
        fd = create(b"aimdo", 1)  # MFD_CLOEXEC, same semantics as upstream.
        if fd < 0:
            error = ctypes.get_errno()
            raise OSError(error, "libc.memfd_create failed: " + os.strerror(error))
        try:
            file = os.fdopen(fd, "w+b", buffering=0)
        except BaseException:
            os.close(fd)
            raise
        try:
            file.truncate(size)
            return file
        except BaseException:
            file.close()
            raise

    module.create_anonymous_file = create_anonymous_file
    # Older utils uses `from comfy.anonymous_file import create_anonymous_file`.
    if utils is not None and getattr(utils, "create_anonymous_file", None) is original:
        utils.create_anonymous_file = create_anonymous_file
    logging.info("[canvas-onekey] Enabled libc memfd compatibility (Python os.memfd_create missing).")
    return True


if sys.platform == "linux" and not callable(getattr(os, "memfd_create", None)):
    try:
        import comfy.anonymous_file as anonymous_file
        install(anonymous_file, sys.modules.get("comfy.utils"))
    except ImportError:
        # Old releases that never used anonymous files do not need this patch.
        pass
