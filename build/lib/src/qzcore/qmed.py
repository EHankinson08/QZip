#!/usr/bin/env python3
"""QMED: lossless NIfTI candidate for a private QZip safe_compress hook.

Layout: NIfTI header is stored separately from voxels. Integer-valued float
voxels are recast (exact) to a compact integer dtype. A reversible 3D
predictor is applied, then an optional byte-shuffle, then the smallest of
several backends.

License of this module: original QZip candidate code.
Backends:
  xz / lzma  — public domain (XZ Utils / liblzma)
  zstd       — BSD-3-Clause (Facebook zstd CLI)
  libbsc     — Apache-2.0, Ilya Grebnov 3.4.1 (optional, via vendored libqznative.so)
  bzip2      — BSD-style (Python bz2)
"""
from __future__ import annotations

import bz2
import lzma
import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

MAGIC = b"QMED"
VERSION = 1

# packed voxel dtypes
PACK_U8, PACK_I16, PACK_I32, PACK_F32, PACK_F64, PACK_I16F = 1, 2, 3, 4, 5, 6
# predictors
PRED_NONE, PRED_DZ, PRED_PLANAR3D, PRED_XDZ = 0, 1, 2, 3
# backends
BE_XZ9, BE_XZ9E, BE_ZSTD19L, BE_BSC, BE_BZ2 = 1, 2, 3, 4, 5

PRED_NAME = {PRED_NONE: "none", PRED_DZ: "dz", PRED_PLANAR3D: "planar3d", PRED_XDZ: "xdz"}
BE_NAME = {BE_XZ9: "xz-9", BE_XZ9E: "xz-9e", BE_ZSTD19L: "zstd-19-long", BE_BSC: "bsc", BE_BZ2: "bzip2"}
PACK_NAME = {PACK_U8: "u8", PACK_I16: "i16", PACK_I32: "i32", PACK_F32: "f32", PACK_F64: "f64", PACK_I16F: "i16f"}

NIFTI_DTYPE = {
    2: np.uint8, 4: np.int16, 8: np.int32, 16: np.float32, 64: np.float64,
    256: np.int8, 512: np.uint16, 768: np.uint32,
}

_BSC = None


def _load_bsc():
    global _BSC
    if _BSC is not None:
        return _BSC
    here = Path(__file__).resolve().parent
    candidates = [
        here / "native" / "qz_native.py",
    ]
    for p in candidates:
        if p.is_file():
            sys.path.insert(0, str(p.parent))
            import qz_native  # type: ignore
            _BSC = qz_native
            return _BSC
    return None


def parse_nifti(data: bytes):
    if len(data) < 352:
        raise ValueError("too small to be NIfTI-1")
    sizeof = struct.unpack_from("<i", data, 0)[0]
    endian = "<"
    if sizeof == 348 or sizeof == 540:
        endian = "<"
    else:
        sizeof_be = struct.unpack_from(">i", data, 0)[0]
        if sizeof_be in (348, 540):
            endian = ">"
            sizeof = sizeof_be
        else:
            raise ValueError(f"not a NIfTI header (sizeof_hdr={sizeof})")
    dim = struct.unpack_from(endian + "8h", data, 40)
    datatype = struct.unpack_from(endian + "h", data, 70)[0]
    bitpix = struct.unpack_from(endian + "h", data, 72)[0]
    vox_offset = int(struct.unpack_from(endian + "f", data, 108)[0])
    if vox_offset < sizeof:
        vox_offset = 352 if sizeof <= 352 else sizeof
    ndim = int(dim[0])
    nx, ny, nz = int(dim[1] or 1), int(dim[2] or 1), int(dim[3] or 1)
    if ndim >= 4 and int(dim[4] or 1) > 1:
        # treat 4th dim as stacked slices
        nz = nz * int(dim[4])
    npdt = NIFTI_DTYPE.get(int(datatype))
    if npdt is None:
        raise ValueError(f"unsupported NIfTI datatype {datatype}")
    header = data[:vox_offset]
    voxels = data[vox_offset:]
    expected = nx * ny * nz * (bitpix // 8)
    if expected <= 0 or len(voxels) < expected:
        # fall back: infer from remaining bytes
        bp = bitpix // 8 or np.dtype(npdt).itemsize
        total = len(voxels) // bp
        nz = max(1, total // (nx * ny)) if nx * ny else 1
        expected = nx * ny * nz * bp
        voxels = voxels[:expected]
    else:
        voxels = voxels[:expected]
    vol = np.frombuffer(voxels, dtype=npdt).reshape((nz, ny, nx))
    if endian == ">" and vol.dtype.byteorder != "=":
        vol = vol.byteswap().newbyteorder()
    return {
        "endian": endian,
        "sizeof": sizeof,
        "datatype": int(datatype),
        "bitpix": int(bitpix),
        "vox_offset": vox_offset,
        "shape": (nz, ny, nx),  # z,y,x
        "header": header,
        "vol": vol,
        "tail": data[vox_offset + expected :],
        "orig_len": len(data),
    }


def _byte_identical_recast(packed, orig: np.ndarray) -> bool:
    """True only if expanding packed back to orig.dtype reproduces orig's raw bytes (rejects -0.0 mismatches)."""
    rec = packed.astype(orig.dtype, copy=False)
    return np.ascontiguousarray(rec).tobytes() == np.ascontiguousarray(orig).tobytes()


def try_int_pack(vol: np.ndarray):
    """Return (packed_array, pack_id) if a lossless integer recast exists, else None."""
    if np.issubdtype(vol.dtype, np.integer):
        if vol.dtype == np.uint8:
            return vol, PACK_U8
        if vol.dtype == np.int16:
            return vol, PACK_I16
        mn, mx = int(vol.min()), int(vol.max())
        if 0 <= mn and mx <= 255:
            p = vol.astype(np.uint8)
            if _byte_identical_recast(p, vol):
                return p, PACK_U8
        if -32768 <= mn and mx <= 32767:
            p = vol.astype(np.int16)
            if _byte_identical_recast(p, vol):
                return p, PACK_I16
        if vol.dtype == np.int32:
            return vol, PACK_I32
        p = vol.astype(np.int32, copy=False)
        if _byte_identical_recast(p, vol):
            return p, PACK_I32
        return None
    # floats: exact integer values only, and byte-identical after recast (no -0.0 surprises)
    if not np.issubdtype(vol.dtype, np.floating):
        return None
    if not np.isfinite(vol).all():
        return None
    rounded = np.round(vol)
    if not np.equal(vol, rounded).all():
        return None
    mn, mx = float(rounded.min()), float(rounded.max())
    trials = []
    if 0 <= mn and mx <= 255:
        trials.append((rounded.astype(np.uint8), PACK_U8))
    if -32768 <= mn and mx <= 32767:
        trials.append((rounded.astype(np.int16), PACK_I16))
    if -2147483648 <= mn and mx <= 2147483647:
        trials.append((rounded.astype(np.int32), PACK_I32))
    for packed, pid in trials:
        if _byte_identical_recast(packed, vol):
            return packed, pid
    return None



def try_mostly_int_pack(vol: np.ndarray):
    """int16 plane of rounded values plus a sparse exact-float patch for non-integer (or -0.0) voxels."""
    if not np.issubdtype(vol.dtype, np.floating) or not np.isfinite(vol).all():
        return None
    rounded = np.round(vol)
    mn, mx = float(rounded.min()), float(rounded.max())
    if mn < -32768 or mx > 32767:
        return None
    ints = rounded.astype(np.int16)
    rec = ints.astype(vol.dtype, copy=False)
    vol_c = np.ascontiguousarray(vol)
    rec_c = np.ascontiguousarray(rec)
    mask = rec_c.view(np.uint64 if vol.dtype == np.float64 else np.uint32) != vol_c.view(np.uint64 if vol.dtype == np.float64 else np.uint32)
    n = int(mask.sum())
    if n == 0 or n > mask.size // 4:
        return None
    extras = np.ascontiguousarray(vol_c[mask]).tobytes()
    mask_bits = np.packbits(mask.ravel().astype(np.uint8))
    extra = struct.pack("<I", n) + mask_bits.tobytes() + extras
    # verify reconstruct
    out = rec_c.copy()
    out[mask] = np.frombuffer(extras, dtype=vol.dtype)
    if np.ascontiguousarray(out).tobytes() != vol_c.tobytes():
        return None
    return ints, PACK_I16F, extra


def residual_xdz(vol: np.ndarray) -> np.ndarray:
    """Bit-xor of consecutive slices (lossless for any float/int dtype)."""
    item = vol.dtype.itemsize
    u = {8: np.uint64, 4: np.uint32, 2: np.uint16, 1: np.uint8}[item]
    work = np.ascontiguousarray(vol).view(u).reshape(vol.shape)
    out = np.empty_like(work)
    out[0] = work[0]
    out[1:] = work[1:] ^ work[:-1]
    return out


def invert_xdz(res: np.ndarray, out_dtype) -> np.ndarray:
    item = np.dtype(out_dtype).itemsize
    u = {8: np.uint64, 4: np.uint32, 2: np.uint16, 1: np.uint8}[item]
    work = np.ascontiguousarray(res).view(u).reshape(res.shape).copy()
    for z in range(1, work.shape[0]):
        work[z] = work[z] ^ work[z - 1]
    return work.view(out_dtype).reshape(res.shape)


def byteshuffle(arr: np.ndarray) -> bytes:
    a = np.ascontiguousarray(arr)
    raw = a.view(np.uint8).reshape(a.size, a.dtype.itemsize)
    return np.ascontiguousarray(raw.T).tobytes()


def unshuffle(buf: bytes, dtype, shape) -> np.ndarray:
    item = np.dtype(dtype).itemsize
    n = int(np.prod(shape))
    raw = np.frombuffer(buf, dtype=np.uint8)
    if raw.size != n * item:
        raise ValueError("unshuffle size mismatch")
    planes = raw.reshape(item, n)
    return np.ascontiguousarray(planes.T).reshape(-1).view(dtype).reshape(shape)


def residual_dz(vol: np.ndarray) -> np.ndarray:
    """Slice-delta residual in a wider signed int dtype; first slice stored raw (same width)."""
    work = vol.astype(np.int32, copy=True)
    # first slice stays as-is; later slices are current - previous *original*
    out = np.empty_like(work)
    out[0] = work[0]
    out[1:] = work[1:] - work[:-1]
    return out


def invert_dz(res: np.ndarray, out_dtype) -> np.ndarray:
    work = res.astype(np.int32, copy=True)
    for z in range(1, work.shape[0]):
        work[z] = work[z] + work[z - 1]
    return work.astype(out_dtype, copy=False)


def _delta_axis(arr: np.ndarray, axis: int) -> np.ndarray:
    """First difference along axis; first plane kept raw. Vectorized, lossless with cumsum."""
    out = arr.astype(np.int32, copy=True)
    sl_in = [slice(None)] * out.ndim
    sl_prev = [slice(None)] * out.ndim
    sl_in[axis] = slice(1, None)
    sl_prev[axis] = slice(None, -1)
    out[tuple(sl_in)] = out[tuple(sl_in)] - out[tuple(sl_prev)]
    return out


def _undelta_axis(arr: np.ndarray, axis: int) -> np.ndarray:
    return np.cumsum(arr, axis=axis, dtype=np.int32)


def residual_planar3d(vol: np.ndarray) -> np.ndarray:
    """dz, then dy, then dx (all first-differences). Fully vectorized and invertible."""
    return _delta_axis(_delta_axis(residual_dz(vol), 1), 2)


def invert_planar3d(res: np.ndarray, out_dtype) -> np.ndarray:
    work = _undelta_axis(_undelta_axis(res.astype(np.int32, copy=False), 2), 1)
    return invert_dz(work, out_dtype)


def _narrow_residual(res: np.ndarray):
    mn, mx = int(res.min()), int(res.max())
    if -32768 <= mn and mx <= 32767:
        return res.astype(np.int16), PACK_I16
    return res.astype(np.int32), PACK_I32


def apply_pred(packed: np.ndarray, pred: int):
    if pred == PRED_NONE:
        return packed, None  # store packed dtype as-is
    if pred == PRED_XDZ:
        return residual_xdz(packed), packed.dtype
    if pred == PRED_DZ:
        return residual_dz(packed), packed.dtype
    if pred == PRED_PLANAR3D:
        return residual_planar3d(packed), packed.dtype
    raise ValueError(pred)


def invert_pred(res: np.ndarray, pred: int, packed_dtype):
    if pred == PRED_NONE:
        return res.astype(packed_dtype, copy=False)
    if pred == PRED_XDZ:
        return invert_xdz(res, packed_dtype)
    if pred == PRED_DZ:
        return invert_dz(res, packed_dtype)
    if pred == PRED_PLANAR3D:
        return invert_planar3d(res, packed_dtype)
    raise ValueError(pred)


def _be_compress(data: bytes, backend: int) -> bytes:
    if backend == BE_XZ9:
        return lzma.compress(data, preset=9)
    if backend == BE_XZ9E:
        return lzma.compress(data, preset=9 | lzma.PRESET_EXTREME)
    if backend == BE_ZSTD19L:
        r = subprocess.run(
            ["zstd", "-19", "--long=27", "-q", "-c"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode()[:200])
        return r.stdout
    if backend == BE_BSC:
        lib = _load_bsc()
        if lib is None:
            raise RuntimeError("libbsc not available")
        # limit OpenMP threads; caller should also set OMP_NUM_THREADS
        return lib.bsc_compress(data, threads=2)
    if backend == BE_BZ2:
        return bz2.compress(data, 9)
    raise ValueError(backend)


def _be_decompress(data: bytes, backend: int) -> bytes:
    if backend in (BE_XZ9, BE_XZ9E):
        return lzma.decompress(data)
    if backend == BE_ZSTD19L:
        r = subprocess.run(
            ["zstd", "-d", "--long=27", "-q", "-c"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode()[:200])
        return r.stdout
    if backend == BE_BSC:
        lib = _load_bsc()
        if lib is None:
            raise RuntimeError("libbsc not available")
        return lib.bsc_decompress(data, threads=2)
    if backend == BE_BZ2:
        return bz2.decompress(data)
    raise ValueError(backend)


def _pack_id_dtype(pack_id: int):
    return {PACK_U8: np.uint8, PACK_I16: np.int16, PACK_I16F: np.int16, PACK_I32: np.int32,
            PACK_F32: np.float32, PACK_F64: np.float64}[pack_id]


def wrap_container(header: bytes, tail: bytes, body: bytes, meta: dict) -> bytes:
    """self-describing lossless blob."""
    hdr_c = lzma.compress(header, preset=9)
    tail_c = lzma.compress(tail, preset=9) if tail else b""
    # header: magic(4) ver(1) pack(1) pred(1) backend(1) shuffle(1) res_pack(1)
    #         nz,ny,nx (3I) vox_offset(I) orig_len(I) datatype(H) bitpix(H)
    #         hdr_c_len(I) tail_c_len(I) body_len(I)
    nz, ny, nx = meta["shape"]
    pref = MAGIC + struct.pack(
        "<BBBBBBIII I I HH III",
        VERSION,
        meta["pack"],
        meta["pred"],
        meta["backend"],
        1 if meta["shuffle"] else 0,
        meta["res_pack"],
        nz, ny, nx,
        meta["vox_offset"],
        meta["orig_len"],
        meta["datatype"],
        meta["bitpix"],
        len(hdr_c),
        len(tail_c),
        len(body),
    )
    return pref + hdr_c + tail_c + body


def unwrap_container(blob: bytes):
    if blob[:4] != MAGIC:
        raise ValueError("not a QMED blob")
    off = 4
    ver, pack, pred, backend, shuffle, res_pack = struct.unpack_from("<BBBBBB", blob, off)
    off += 6
    nz, ny, nx, vox_offset, orig_len, datatype, bitpix, hdr_len, tail_len, body_len = struct.unpack_from(
        "<III I I HH III", blob, off
    )
    off += 3 * 4 + 4 + 4 + 2 + 2 + 3 * 4
    hdr_c = blob[off:off + hdr_len]; off += hdr_len
    tail_c = blob[off:off + tail_len]; off += tail_len
    body = blob[off:off + body_len]
    header = lzma.decompress(hdr_c)
    tail = lzma.decompress(tail_c) if tail_len else b""
    return {
        "ver": ver, "pack": pack, "pred": pred, "backend": backend,
        "shuffle": bool(shuffle), "res_pack": res_pack,
        "shape": (nz, ny, nx), "vox_offset": vox_offset, "orig_len": orig_len,
        "datatype": datatype, "bitpix": bitpix,
        "header": header, "tail": tail, "body": body,
    }


def encode_with(info: dict, packed: np.ndarray, pack_id: int, pred: int, backend: int, shuffle: bool, extra: bytes = b""):
    res, _pdt = apply_pred(packed, pred)
    if pred in (PRED_NONE, PRED_XDZ):
        res_store = packed if pred == PRED_NONE else res
        res_pack = pack_id if pred == PRED_NONE else {
            PACK_U8: PACK_U8, PACK_I16: PACK_I16, PACK_I16F: PACK_I16, PACK_I32: PACK_I32,
            PACK_F32: PACK_F32, PACK_F64: PACK_F64,
        }.get(pack_id, pack_id)
        if pred == PRED_XDZ:
            # xor residual stays the same width as the packed array
            res_pack = {2: PACK_I16, 4: PACK_F32, 8: PACK_F64, 1: PACK_U8}.get(res_store.dtype.itemsize, res_pack)
            if pack_id == PACK_I16F:
                res_pack = PACK_I16
    else:
        res_store, res_pack = _narrow_residual(res)
    raw = byteshuffle(res_store) if shuffle else np.ascontiguousarray(res_store).tobytes()
    body = _be_compress(raw, backend)
    if extra:
        extra_c = _be_compress(extra, backend)
        body = struct.pack("<I", len(extra_c)) + extra_c + body
    meta = {
        "pack": pack_id, "pred": pred, "backend": backend, "shuffle": shuffle,
        "res_pack": res_pack, "shape": info["shape"], "vox_offset": info["vox_offset"],
        "orig_len": info["orig_len"], "datatype": info["datatype"], "bitpix": info["bitpix"],
        "has_extra": bool(extra),
    }
    blob = wrap_container(info["header"], info["tail"], body, meta)
    return blob, {
        "method": f"qmed:{PACK_NAME[pack_id]}+{PRED_NAME[pred]}+{'shuf' if shuffle else 'noshuf'}+{BE_NAME[backend]}",
        "payload": len(blob), "body": len(body),
        "pack": PACK_NAME[pack_id], "pred": PRED_NAME[pred],
        "backend": BE_NAME[backend], "shuffle": shuffle,
    }


def decode(blob: bytes) -> bytes:
    u = unwrap_container(blob)
    body = u["body"]
    extra = b""
    if u["pack"] == PACK_I16F:
        elen = struct.unpack_from("<I", body, 0)[0]
        extra = _be_decompress(body[4:4 + elen], u["backend"])
        body = body[4 + elen:]
    raw = _be_decompress(body, u["backend"])
    res_dt = _pack_id_dtype(u["res_pack"])
    shape = u["shape"]
    if u["shuffle"]:
        res = unshuffle(raw, res_dt, shape)
    else:
        res = np.frombuffer(raw, dtype=res_dt).reshape(shape)
    packed_dt = np.int16 if u["pack"] == PACK_I16F else _pack_id_dtype(u["pack"])
    packed = invert_pred(res, u["pred"], packed_dt)
    # recast back to original on-disk dtype
    orig_dt = NIFTI_DTYPE[u["datatype"]]
    vol = packed.astype(orig_dt, copy=True)
    if u["pack"] == PACK_I16F:
        n = struct.unpack_from("<I", extra, 0)[0]
        mask_bytes = (int(np.prod(shape)) + 7) // 8
        mask = np.unpackbits(np.frombuffer(extra[4:4 + mask_bytes], dtype=np.uint8))[: int(np.prod(shape))]
        mask = mask.astype(bool).reshape(shape)
        floats = np.frombuffer(extra[4 + mask_bytes:], dtype=orig_dt)
        if int(mask.sum()) != n or floats.size != n:
            raise ValueError(f"i16f extra mismatch n={n} mask={int(mask.sum())} floats={floats.size}")
        vol[mask] = floats
    out = u["header"] + np.ascontiguousarray(vol).tobytes() + u["tail"]
    if len(out) != u["orig_len"]:
        raise ValueError(f"length mismatch {len(out)} vs {u['orig_len']}")
    return out


# Default search used when compressing one volume. Keep this small: each
# xz/bsc pass on ~18 MB is seconds, not minutes, after int packing.
DEFAULT_GRID = [
    # pack decided at runtime; these are (pred, backend, shuffle)
    (PRED_DZ, BE_BSC, True),
    (PRED_PLANAR3D, BE_BSC, True),
    (PRED_NONE, BE_BSC, True),
    (PRED_DZ, BE_ZSTD19L, True),
    (PRED_PLANAR3D, BE_ZSTD19L, True),
    (PRED_DZ, BE_XZ9, True),
    (PRED_PLANAR3D, BE_XZ9, True),
    (PRED_DZ, BE_BZ2, True),
]


def compress(data: bytes, grid=None, log=None):
    """Return (blob, info) for the smallest byte-exact candidate in the grid."""
    info = parse_nifti(data)
    packed_pair = try_int_pack(info["vol"])
    mostly = try_mostly_int_pack(info["vol"]) if packed_pair is None else None
    candidates = []
    plans = []  # (packed, pack_id, pred, backend, shuffle, extra)
    if packed_pair is not None:
        packed, pack_id = packed_pair
        for pred, backend, shuffle in (grid or DEFAULT_GRID):
            plans.append((packed, pack_id, pred, backend, shuffle, b""))
    if mostly is not None:
        packed, pack_id, extra = mostly
        for pred, backend, shuffle in (grid or DEFAULT_GRID):
            plans.append((packed, pack_id, pred, backend, shuffle, extra))
    raw_pack = {np.dtype("u1"): PACK_U8, np.dtype("i2"): PACK_I16, np.dtype("i4"): PACK_I32,
                np.dtype("f4"): PACK_F32, np.dtype("f8"): PACK_F64}.get(info["vol"].dtype)
    if raw_pack in (PACK_F32, PACK_F64) or packed_pair is None:
        if raw_pack is not None:
            plans.append((info["vol"], raw_pack, PRED_NONE, BE_BSC, True, b""))
            plans.append((info["vol"], raw_pack, PRED_NONE, BE_XZ9, True, b""))
            plans.append((info["vol"], raw_pack, PRED_XDZ, BE_BSC, True, b""))
            plans.append((info["vol"], raw_pack, PRED_XDZ, BE_XZ9, True, b""))

    best = None
    tried = []
    for packed, pack_id, pred, backend, shuffle, extra in plans:
        name = f"{PACK_NAME[pack_id]}+{PRED_NAME[pred]}+{'shuf' if shuffle else 'noshuf'}+{BE_NAME[backend]}"
        try:
            blob, meta = encode_with(info, packed, pack_id, pred, backend, shuffle, extra=extra)
            out = decode(blob)
            ok = out == data
            tried.append((name, meta["payload"], ok))
            if log:
                log(f"  qmed {name}: {meta['payload']} exact={ok}")
            if not ok:
                continue
            if best is None or meta["payload"] < best[1]["payload"]:
                best = (blob, meta)
        except Exception as e:
            tried.append((name, f"error:{type(e).__name__}:{e}", False))
            if log:
                log(f"  qmed {name}: ERROR {type(e).__name__}: {e}")
    if best is None:
        raise RuntimeError(f"no byte-exact QMED candidate; tried={tried}")
    best[1]["tried"] = tried
    return best
