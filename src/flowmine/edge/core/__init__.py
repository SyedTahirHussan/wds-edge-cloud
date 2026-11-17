"""MicroPython-compatible edge core.

Rules for every module in this package: pure Python, no third-party imports, no
``typing``/``dataclasses``, constant memory per sample apart from fixed-size buffers.
The same tests run under CPython 3.12 and the MicroPython Unix port.
"""
