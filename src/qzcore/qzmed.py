#!/usr/bin/env python3
"""qzmed: QMED transforms (header split, exact int recast, 3D inter-slice / Lorenzo delta, byte shuffle)
with qzcore-owned backends only: QZB (fast, word-transform-free libbsc fork) and QZC (our CM, max).
Selection: every transform is scored with QZB (fast), the best two are re-coded with QZC, smallest
byte-exact candidate wins. Decode is sha256-checked before the file is written.
Usage: qzmed.py c|d in out      env: QZC_BIN, QZB_BIN, QZMED_LEVEL (2), QZMED_TOPK (2)"""
import os, sys, subprocess, tempfile, hashlib, struct
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, qmed

D = os.path.dirname(os.path.abspath(__file__))
_X = ".exe" if os.name == "nt" else ""
QZC = os.environ.get("QZC_BIN", os.path.join(D, "qzc" + _X)); QZB = os.environ.get("QZB_BIN", os.path.join(D, "qzb" + _X))
LEVEL = os.environ.get("QZMED_LEVEL", "0"); TOPK = int(os.environ.get("QZMED_TOPK", "2"))
BE_QZB, BE_QZC = 7, 8
qmed.BE_NAME[BE_QZB] = "qzb"; qmed.BE_NAME[BE_QZC] = "qzc"

def _run(args, data):
    with tempfile.TemporaryDirectory(prefix="qzmed_") as t:
        i, o = os.path.join(t, "i"), os.path.join(t, "o")
        open(i, "wb").write(data)
        subprocess.run(args(i, o), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return open(o, "rb").read()

_oc, _od = qmed._be_compress, qmed._be_decompress
def _c(data, be):
    if be == BE_QZB: return _run(lambda i, o: [QZB, "c", i, o, "0"], data)
    if be == BE_QZC: return _run(lambda i, o: [QZC, "c", i, o, "0", "0", LEVEL], data)
    raise ValueError("qzmed uses only qzcore backends")
def _d(data, be):
    if be == BE_QZB: return _run(lambda i, o: [QZB, "d", i, o], data)
    if be == BE_QZC: return _run(lambda i, o: [QZC, "d", i, o], data)
    raise ValueError(be)
qmed._be_compress, qmed._be_decompress = _c, _d

def plans(info):
    vol = info["vol"]; out = []
    pp = qmed.try_int_pack(vol); mostly = qmed.try_mostly_int_pack(vol) if pp is None else None
    preds = (qmed.PRED_DZ, qmed.PRED_PLANAR3D, qmed.PRED_NONE)
    for src in ([(pp[0], pp[1], b"")] if pp else []) + ([mostly] if mostly else []):
        for pr in preds:
            for sh in (True, False): out.append((src[0], src[1], pr, sh, src[2]))
    rp = {np.dtype("u1"): qmed.PACK_U8, np.dtype("i2"): qmed.PACK_I16, np.dtype("i4"): qmed.PACK_I32,
          np.dtype("f4"): qmed.PACK_F32, np.dtype("f8"): qmed.PACK_F64}.get(vol.dtype)
    if rp is not None and (rp in (qmed.PACK_F32, qmed.PACK_F64) or pp is None):
        for pr in (qmed.PRED_NONE, qmed.PRED_XDZ): out.append((vol, rp, pr, True, b""))
    return out

def compress(src, dst):
    data = open(src, "rb").read(); info = qmed.parse_nifti(data); P = plans(info)
    def enc(p, be):
        blob, meta = qmed.encode_with(info, p[0], p[1], p[2], be, p[3], extra=p[4]); return len(blob), blob, meta["method"]
    with ThreadPoolExecutor(4) as ex: fast = list(ex.map(lambda p: enc(p, BE_QZB), P))
    order = sorted(range(len(P)), key=lambda k: fast[k][0])
    cands = [fast[order[0]]]
    with ThreadPoolExecutor(TOPK) as ex: cands += list(ex.map(lambda k: enc(P[k], BE_QZC), order[:TOPK]))
    want = hashlib.sha256(data).digest()
    for size, blob, method in sorted(cands, key=lambda c: c[0]):
        if hashlib.sha256(qmed.decode(blob)).digest() == want:
            open(dst, "wb").write(blob); sys.stderr.write("qzmed %s: %d -> %d %s\n" % (os.path.basename(src), len(data), size, method)); return
    sys.exit("qzmed: no byte-exact candidate")

if __name__ == "__main__":
    m, a, b = sys.argv[1:4]
    if m == "c": compress(a, b)
    else: open(b, "wb").write(qmed.decode(open(a, "rb").read()))
