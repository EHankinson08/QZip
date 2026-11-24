#!/usr/bin/env python3
"""Block-parallel and low-memory modes for QZip's strong engine (qzmf, the GPL mcm fork).

  python3 src/qzpar.py c [-T threads] [--ram-mb MB] [--level L] <in> <out>
  python3 src/qzpar.py d [-T threads] <in> <out>

The input is cut into T chunks (at line ends when the data is text, so the fork's CSV tracker sees whole rows),
each chunk is coded by its own qzmf process, and the processes run in parallel; decoding is parallel the same way.
The qzmf memory level is the largest one whose total RAM (T processes x ~3 x 2^L MB, measured) fits --ram-mb
(default: 3/4 of physical RAM), and never more than the chunk needs. -T 1 --ram-mb 512 is the low-memory mode.

Format "QZP1": magic | u8 version=1 | u32 nchunks | per chunk: u64 rawlen, u64 clen, 32-byte sha256 | payloads
(each payload is a complete qzmf stream; the sha256 of each chunk is checked on decode).
"""
import concurrent.futures as cf, hashlib, os, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
QZMF = os.environ.get("QZMF_BIN", os.path.join(HERE, "native", "qzmf" + (".exe" if os.name == "nt" else "")))
MAGIC = b"QZP1"
MB = 1 << 20

def phys_mb():
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // MB
    except (ValueError, OSError, AttributeError):
        return 8192

def level_for(ram_mb, threads, chunk_bytes):
    """qzmf -qL peak RSS, measured on the box (5 MB chunk): about 3 x 2^L + 64 MB (L4/L5 ~190 MB, L7 ~445 MB,
    L9 ~1.6 GB, L11 ~6.1 GB on enwik8). Pick the largest L whose T processes fit ram_mb (min L4)."""
    per = ram_mb / max(threads, 1) - 64
    best = 4
    for L in range(4, 12):
        if 3 * (1 << L) <= per:
            best = L
    return best

def split(data, T):
    n = len(data)
    if T <= 1 or n < 2 * MB:
        return [data]
    size = -(-n // T)
    texty = data[:65536].count(b"\n") > 16
    cuts, p = [0], 0
    for _ in range(T - 1):
        e = min(n, p + size)
        if texty and e < n:
            q = data.find(b"\n", e)
            e = n if q < 0 else q + 1
        if e >= n:
            break
        cuts.append(e)
        p = e
    cuts.append(n)
    return [data[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]

def _run(args, cwd):
    r = subprocess.run([QZMF] + args, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode:
        raise RuntimeError("qzmf failed: " + r.stderr.decode(errors="replace")[-300:])

def _enc(chunk, level):
    with tempfile.TemporaryDirectory(prefix="qzpar_") as d:
        open(os.path.join(d, "in"), "wb").write(chunk)
        _run([f"-q{level}", "in", "out"], d)
        return open(os.path.join(d, "out"), "rb").read()

def _dec(blob):
    with tempfile.TemporaryDirectory(prefix="qzpar_") as d:
        open(os.path.join(d, "blob"), "wb").write(blob)
        os.mkdir(os.path.join(d, "x"))
        _run(["d", "../blob", "restored"], os.path.join(d, "x"))
        return open(os.path.join(d, "x", "in"), "rb").read()   # qzmf restores the stored name

def compress(data, threads=1, ram_mb=None, level=None):
    ram_mb = ram_mb or phys_mb() * 3 // 4
    chunks = split(data, threads)
    L = level if level is not None else level_for(ram_mb, len(chunks), max(len(c) for c in chunks))
    with cf.ThreadPoolExecutor(len(chunks)) as ex:
        blobs = list(ex.map(lambda c: _enc(c, L), chunks))
    head = MAGIC + bytes([1]) + struct.pack("<I", len(chunks))
    for c, b in zip(chunks, blobs):
        head += struct.pack("<QQ", len(c), len(b)) + hashlib.sha256(c).digest()
    return head + b"".join(blobs), L

def decompress(blob, threads=None):
    if blob[:4] != MAGIC or blob[4] != 1:
        raise ValueError("not a QZP1 stream")
    (k,) = struct.unpack("<I", blob[5:9])
    p, meta = 9, []
    for _ in range(k):
        rl, cl = struct.unpack("<QQ", blob[p:p + 16]); meta.append((rl, cl, blob[p + 16:p + 48])); p += 48
    parts = []
    for rl, cl, h in meta:
        parts.append(blob[p:p + cl]); p += cl
    with cf.ThreadPoolExecutor(threads or len(parts)) as ex:
        outs = list(ex.map(_dec, parts))
    for (rl, cl, h), o in zip(meta, outs):
        if len(o) != rl or hashlib.sha256(o).digest() != h:
            raise ValueError("chunk checksum mismatch")
    return b"".join(outs)

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["c", "d"])
    ap.add_argument("-T", type=int, default=1)
    ap.add_argument("--ram-mb", type=int, default=None)
    ap.add_argument("--level", type=int, default=None)
    ap.add_argument("inp"); ap.add_argument("out")
    a = ap.parse_args(argv)
    data = open(a.inp, "rb").read()
    if a.cmd == "c":
        blob, L = compress(data, a.T, a.ram_mb, a.level)
        print(f"qzpar: {len(data)} -> {len(blob)} B, chunks={min(a.T, max(1, len(data) // MB))} level={L}", file=sys.stderr)
    else:
        blob = decompress(data, a.T)
    open(a.out, "wb").write(blob)

if __name__ == "__main__":
    main(sys.argv[1:])
