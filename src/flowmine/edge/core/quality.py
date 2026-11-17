"""Per-sample quality code: one integer, flag bits 0-7 and model version in bits 8-15.

The bits follow the order of the validation tests in pipeline.py, so a code read
from the wire tells which test rejected a sample.
"""

MISSING = 1
OUT_OF_RANGE = 2
RATE = 4
FLATLINE = 8
MODEL_RESIDUAL = 16
CROSSCHECK = 32
RECONSTRUCTED = 64
SHIFT = 128

FLAG_MASK = 0xFF
REJECTING = OUT_OF_RANGE | RATE | FLATLINE | MODEL_RESIDUAL

NAMES = (
    (MISSING, "MISSING"),
    (OUT_OF_RANGE, "OUT_OF_RANGE"),
    (RATE, "RATE"),
    (FLATLINE, "FLATLINE"),
    (MODEL_RESIDUAL, "MODEL_RESIDUAL"),
    (CROSSCHECK, "CROSSCHECK"),
    (RECONSTRUCTED, "RECONSTRUCTED"),
    (SHIFT, "SHIFT"),
)


def make(flags, model_version):
    """Combine flag bits and a model version (kept modulo 256) into one code."""
    return (flags & FLAG_MASK) | ((model_version & 0xFF) << 8)


def flags(code):
    return code & FLAG_MASK


def model_version(code):
    return (code >> 8) & 0xFF


def is_suspect(code):
    """True when the sample was missing or failed a test (cross-check included)."""
    return (code & (MISSING | REJECTING | CROSSCHECK)) != 0


def names(code):
    out = []
    for bit, name in NAMES:
        if code & bit:
            out.append(name)
    return out
