#!/usr/bin/env python3
"""qzcsv: QZip structured (CSV/TSV/log) mode = qtyp type-aware column transform (ours)
+ every stream coded by the qzcore CM engine (ours). No third-party codec in the path.
  qzcsv.py c <in> <out>     qzcsv.py d <in> <out>
Container: b'QZS1' u8 mode (0=whole-file CM, 1=qtyp streams) then
  mode 0: CM blob;  mode 1: u32 nstreams, then per stream: u16 namelen, name, u8 codec(0 stored,1 CM), u64 len, data.
"""
import os, sys, struct, subprocess, tempfile, shutil, hashlib
from concurrent.futures import ThreadPoolExecutor
TH = int(os.environ.get("QZCSV_THREADS", "8"))
HERE = os.path.dirname(os.path.abspath(__file__))
_X = ".exe" if os.name == "nt" else ""
QZC = os.environ.get("QZC_BIN", os.path.join(HERE, "qzc" + _X)); QTYP = os.environ.get("QTYP_BIN", os.path.join(HERE, "qtyp" + _X))

def hbits_for(n):
    return 0  # 0 = qzc sizes its context table to the input (and free RAM); size is stored in the header

def cm(data, tmp, tag):
    p = os.path.join(tmp, tag + ".raw"); q = p + ".q"
    open(p, "wb").write(data)
    subprocess.run([QZC, "c", p, q, str(hbits_for(len(data))), "0", os.environ.get("QZCSV_LEVEL", "0")], check=True, stderr=subprocess.DEVNULL)
    out = open(q, "rb").read(); os.remove(p); os.remove(q); return out

def uncm(blob, tmp, tag):
    q = os.path.join(tmp, tag + ".q"); p = q + ".d"
    open(q, "wb").write(blob); subprocess.run([QZC, "d", q, p], check=True, stderr=subprocess.DEVNULL)
    out = open(p, "rb").read(); os.remove(p); os.remove(q); return out

def enc_stream(name, data, tmp):
    if len(data) < 16: return (0, data)
    c = cm(data, tmp, "s_" + name.replace("/", "_"))
    return (1, c) if len(c) < len(data) else (0, data)

def compress(src, dst):
    raw = open(src, "rb").read()
    tmp = tempfile.mkdtemp(prefix="qzcsv_")
    try:
        ex0 = ThreadPoolExecutor(1)  # whole-file CM runs alongside the typed path (files <= 256 MiB; larger only as fallback)
        fut = ex0.submit(cm, raw, tmp, "whole") if len(raw) <= (256 << 20) else None
        d = os.path.join(tmp, "st"); os.mkdir(d); best = None

        r = subprocess.run([QTYP, "e", src, d], stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            names = sorted(os.listdir(d))
            with ThreadPoolExecutor(TH) as ex: coded = dict(zip(names, ex.map(lambda n: enc_stream(n, open(os.path.join(d, n), "rb").read(), tmp), names)))
            size = lambda n: len(coded[n][1]) + len(n) + 11
            keep = set(names)
            cols = sorted({n.split(".")[0] for n in names if n.startswith("c") and "." in n})
            for c in cols:   # per column choose: typed streams vs raw fallback (.x); for numbers .n vs .t
                x = c + ".x"
                for nn in [n for n in names if n.startswith(c + ".n")]:
                    tt = c + ".t" + nn.split(".n")[1]
                    if tt in coded:
                        if size(nn) <= size(tt): keep.discard(tt)
                        else: keep.discard(nn)
                if x in coded:
                    typed = [n for n in keep if n.startswith(c + ".") and n != x]
                    if sum(size(n) for n in typed) <= size(x): keep.discard(x)
                    else:
                        for n in typed: keep.discard(n)
            body = bytearray(b"QZS1" + bytes([1]) + struct.pack("<I", len(keep)))
            for n in sorted(keep):
                k, data = coded[n]; nb = n.encode()
                body += struct.pack("<H", len(nb)) + nb + bytes([k]) + struct.pack("<Q", len(data)) + data
            best = (b"QZS1" + bytes([0]) + fut.result()) if fut else None
            # self-check before choosing the structured container
            if best is None or len(body) < len(best):
                open(dst + ".chk", "wb").write(bytes(body))
                decompress(dst + ".chk", dst + ".chk.out")
                ok = hashlib.sha256(open(dst + ".chk.out", "rb").read()).digest() == hashlib.sha256(raw).digest()
                os.remove(dst + ".chk"); os.remove(dst + ".chk.out")
                if ok: best = bytes(body)
                else:
                    sys.stderr.write("qzcsv: structured path failed self-check, using whole-file CM\n")
                    if best is None: best = b"QZS1" + bytes([0]) + cm(raw, tmp, "whole")
        if best is None: best = b"QZS1" + bytes([0]) + (fut.result() if fut else cm(raw, tmp, "whole"))
        open(dst, "wb").write(best)
        sys.stderr.write("qzcsv %s: %d -> %d (mode %d)\n" % (os.path.basename(src), len(raw), len(best), best[4]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def decompress(src, dst):
    b = open(src, "rb").read(); assert b[:4] == b"QZS1"
    tmp = tempfile.mkdtemp(prefix="qzcsvd_")
    try:
        if b[4] == 0:
            open(dst, "wb").write(uncm(b[5:], tmp, "whole")); return
        n, = struct.unpack_from("<I", b, 5); p = 9; d = os.path.join(tmp, "st"); os.mkdir(d)
        items = []
        for i in range(n):
            ln, = struct.unpack_from("<H", b, p); p += 2; name = b[p:p+ln].decode(); p += ln
            k = b[p]; p += 1; L, = struct.unpack_from("<Q", b, p); p += 8; items.append((i, name, k, b[p:p+L])); p += L
        def one(it):
            i, name, k, data = it; open(os.path.join(d, name), "wb").write(uncm(data, tmp, "s%d" % i) if k == 1 else data)
        with ThreadPoolExecutor(TH) as ex: list(ex.map(one, items))
        subprocess.run([QTYP, "d", d, dst], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    (compress if sys.argv[1] == "c" else decompress)(sys.argv[2], sys.argv[3])
