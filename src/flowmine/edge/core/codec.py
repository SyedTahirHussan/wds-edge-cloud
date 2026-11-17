"""Binary uplink frame for LoRaWAN-class links.

Frame = 6-byte header + records.

    header  station u8 | meta u8 | base_time u32
            meta = (n_records - 1) in bits 0-4 (max 32) | model version mod 8 in bits 5-7
    reading sensor u8 (bit 7 clear) | dt u16 | value i16 | quality u8      -> 6 bytes
    summary sensor u8 (bit 7 set)   | dt u16 | mean, min, max i16 | quality u8 -> 10 bytes

A single reading therefore costs 12 bytes. ``dt`` is seconds after ``base_time``
(so one frame spans at most 18 h); values are integers in units of the sensor
resolution, saturated at the int16 range. The payload format version is not in the
frame: on LoRaWAN the FPort field carries it. Quality is the flag byte; the full
model version is kept by the cloud registry, only its low 3 bits travel.
"""

import struct

HEADER = "<BBI"
HEADER_SIZE = 6
READING = "<BHhB"
READING_SIZE = 6
SUMMARY = "<BHhhhB"
SUMMARY_SIZE = 10
MAX_RECORDS = 32
SUMMARY_BIT = 0x80
I16_MIN = -32768
I16_MAX = 32767


def quantise(x, scale):
    q = round(x / scale)
    if q > I16_MAX:
        return I16_MAX
    if q < I16_MIN:
        return I16_MIN
    return q


def record_size(rec):
    return SUMMARY_SIZE if len(rec) == 7 else READING_SIZE


def encode(station, model_version, base_time, records, scales):
    """Encode records into one frame.

    ``records``: tuples ``(sensor, t, value, quality)`` or, for summaries,
    ``(sensor, t, mean, mn, mx, quality, 's')``. ``scales`` maps sensor id to resolution.
    """
    n = len(records)
    if n < 1 or n > MAX_RECORDS:
        raise ValueError("frame needs 1..32 records")
    meta = (n - 1) | ((model_version & 7) << 5)
    out = bytearray(struct.pack(HEADER, station, meta, base_time))
    for rec in records:
        sensor = rec[0]
        dt = rec[1] - base_time
        if dt < 0 or dt > 65535:
            raise ValueError("record outside frame time span")
        s = scales[sensor]
        if len(rec) == 7:
            out += struct.pack(
                SUMMARY,
                sensor | SUMMARY_BIT,
                dt,
                quantise(rec[2], s),
                quantise(rec[3], s),
                quantise(rec[4], s),
                rec[5] & 0xFF,
            )
        else:
            out += struct.pack(READING, sensor, dt, quantise(rec[2], s), rec[3] & 0xFF)
    return bytes(out)


def decode(frame, scales):
    """Return ``(station, model_version_low3, records)``; inverse of ``encode``."""
    station, meta, base = struct.unpack_from(HEADER, frame, 0)
    n = (meta & 31) + 1
    mv = meta >> 5
    off = HEADER_SIZE
    recs = []
    for _ in range(n):
        sensor = frame[off]
        if sensor & SUMMARY_BIT:
            sid, dt, a, b, c, q = struct.unpack_from(SUMMARY, frame, off)
            sid = sid & 0x7F
            s = scales[sid]
            recs.append((sid, base + dt, a * s, b * s, c * s, q, "s"))
            off += SUMMARY_SIZE
        else:
            sid, dt, v, q = struct.unpack_from(READING, frame, off)
            recs.append((sid, base + dt, v * scales[sid], q))
            off += READING_SIZE
    if off != len(frame):
        raise ValueError("trailing bytes in frame")
    return station, mv, recs


def batch(station, model_version, records, scales, max_payload):
    """Split time-ordered records into frames that each fit ``max_payload`` bytes."""
    frames = []
    cur = []
    size = HEADER_SIZE
    for rec in records:
        rs = record_size(rec)
        start = cur[0][1] if cur else rec[1]
        if cur and (size + rs > max_payload or len(cur) == MAX_RECORDS or rec[1] - start > 65535):
            frames.append(encode(station, model_version, cur[0][1], cur, scales))
            cur = []
            size = HEADER_SIZE
        if HEADER_SIZE + rs > max_payload:
            raise ValueError("max_payload smaller than one record")
        cur.append(rec)
        size += rs
    if cur:
        frames.append(encode(station, model_version, cur[0][1], cur, scales))
    return frames
