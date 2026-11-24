#!/usr/bin/env python3
"""Lossless outside codecs on NIfTI volumes: JPEG-LS (CharLS) and reversible JPEG 2000 (OpenJPEG), via imagecodecs.

  med_outside.py FILE.nii [...] >> med_outside.tsv
Rows: file  codec  n  size  enc_s  dec_s  mode  OK|FAIL
The 352-byte NIfTI header is stored raw (counted in size). Voxels are coded slice by slice (axial 2D images):
  * int16 mode  - the volume is integer valued with a <= 16-bit range: (v - min) as uint16 images (+8 B for min)
  * planes mode - anything else (float64 with arbitrary values): the 64-bit words are split into four 16-bit planes
                  (bits 63-48, 47-32, 31-16, 15-0), each coded as uint16 images. Lossless for any float, but the
                  image codecs then model mantissa noise; this is the only lossless route these codecs have.
Every result is decoded and compared byte for byte with the input.
"""
import struct, sys, time
import numpy as np
import imagecodecs as ic

CODECS = {
    "jpegls": (lambda a: ic.jpegls_encode(a, level=0), ic.jpegls_decode),
    "j2k_rev": (lambda a: ic.jpeg2k_encode(a, level=0, reversible=True, numthreads=1),
                lambda b: ic.jpeg2k_decode(b, numthreads=1)),
}

def load(path):
    d = open(path, "rb").read()
    assert struct.unpack("<i", d[:4])[0] == 348, "NIfTI-1 little endian only"
    dim = struct.unpack("<8h", d[40:56]); dt = struct.unpack("<h", d[70:72])[0]; vo = int(struct.unpack("<f", d[108:112])[0])
    np_dt = {2: np.uint8, 4: np.int16, 8: np.int32, 16: np.float32, 64: np.float64, 512: np.uint16}[dt]
    nx, ny, nz = dim[1], dim[2], dim[3]
    vol = np.frombuffer(d, dtype=np_dt, offset=vo, count=nx * ny * nz).reshape(nz, ny, nx)
    return d, vo, vol

def planes(vol):
    a = vol.astype(np.float64, copy=False)
    if (a == np.round(a)).all() and a.max() - a.min() < 65536:
        mn = a.min()
        return "int16", [np.ascontiguousarray((a - mn).astype(np.uint16))], mn
    u = np.ascontiguousarray(vol).view(np.uint64) if vol.dtype.itemsize == 8 else \
        np.ascontiguousarray(vol).view(np.uint32).astype(np.uint64)
    return "planes", [np.ascontiguousarray(((u >> s) & 0xFFFF).astype(np.uint16)) for s in (48, 32, 16, 0)], None

def rebuild(mode, ps, mn, vol):
    if mode == "int16":
        return (ps[0].astype(np.float64) + mn).astype(vol.dtype)
    u = np.zeros(ps[0].shape, np.uint64)
    for p, s in zip(ps, (48, 32, 16, 0)):
        u |= p.astype(np.uint64) << np.uint64(s)
    return u.view(np.float64) if vol.dtype.itemsize == 8 else u.astype(np.uint32).view(np.float32)

def main():
    for path in sys.argv[1:]:
        d, vo, vol = load(path)
        mode, ps, mn = planes(vol)
        for name, (enc, dec) in CODECS.items():
            t = time.perf_counter()
            blobs = [[enc(p[z]) for z in range(p.shape[0])] for p in ps]
            te = time.perf_counter() - t
            size = vo + (8 if mode == "int16" else 0) + sum(len(b) + 4 for bl in blobs for b in bl)  # +4 B length/slice
            t = time.perf_counter()
            out = [np.stack([dec(b) for b in bl]) for bl in blobs]
            v2 = rebuild(mode, out, mn, vol)
            td = time.perf_counter() - t
            ok = d[:vo] + v2.tobytes() + d[vo + vol.nbytes:] == d
            print("\t".join(map(str, [path.rsplit("/", 1)[-1], name, len(d), size, f"{te:.2f}", f"{td:.2f}", mode,
                                       "OK" if ok else "FAIL"])), flush=True)

if __name__ == "__main__":
    main()
