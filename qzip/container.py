"""QZip file container (`.qz`).

Layout (format qzip-2, all new files):
    b"QZIP" | version byte 0x02 | u32 little-endian header length | header (compact UTF-8 JSON) | payload (raw bytes)
The header holds everything except the payload: method, reconstruction_metadata, quantum_parameters (incl. the
qzip block: orig_sha256, orig_size, decoder, route...), orig_sha256, orig_size. The payload is the codec output as
is: no base64, no gzip, so the file is the benchmarked size plus a few hundred bytes of header.

Readers also accept the older containers: JSON with a base64 payload, optionally gzipped and/or space-padded
(CLI `.qc.gz` and web-app `.qc` / `.qc.gz` files, 2025-2026).
"""
import base64, gzip, json, struct

MAGIC = b"QZIP\x02"
EXT = ".qz"
OLD_EXTS = (".qc.gz", ".qc")


def pack(container):
    """Container dict (compressed_data as base64 str or bytes) -> .qz bytes."""
    cd = container.get("compressed_data")
    payload = base64.b64decode(cd) if isinstance(cd, str) else bytes(cd or b"")
    head = {k: v for k, v in container.items() if k != "compressed_data"}
    head["format"] = "qzip-2"
    hb = json.dumps(head, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return MAGIC + struct.pack("<I", len(hb)) + hb + payload


def unpack(raw):
    """.qz bytes or an older .qc / .qc.gz container -> container dict (compressed_data as base64 str)."""
    raw = bytes(raw)
    if raw[:5] == MAGIC:
        (n,) = struct.unpack("<I", raw[5:9])
        if 9 + n > len(raw):
            raise ValueError("truncated .qz header")
        c = json.loads(raw[9:9 + n].decode("utf-8"))
        c["compressed_data"] = base64.b64encode(raw[9 + n:]).decode("ascii")
        return c
    if raw[:4] == b"QZIP":
        raise ValueError(f"unsupported .qz version {raw[4]} (newer QZip?)")
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8").rstrip())   # web .qc files carry trailing space padding


def strip_ext(name):
    for suf in (EXT,) + OLD_EXTS:
        if name.endswith(suf):
            return name[: -len(suf)]
    return None
