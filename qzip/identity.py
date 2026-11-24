"""Identity fields for `.qz` archives: original basename, a sanitized relative source path, and download names.

The qzip-2 header is compact UTF-8 JSON. Extra keys are ignored by older readers (v1.0.0 and earlier),
so adding `original_filename` and `source_path` is backward compatible. Absolute paths and `..`
components are never stored.
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote

MAX_SOURCE_PATH = 1024
SHORT_SHA_LEN = 6

_DRIVE = re.compile(r"^[A-Za-z]:[/\\]?")
_UNC = re.compile(r"^//[^/]+/[^/]+/")


def short_sha(sha256, n=SHORT_SHA_LEN):
    if not sha256:
        return ""
    s = str(sha256).strip().lower()
    if s.startswith("sha256:"):
        s = s.split(":", 1)[1]
    return s[: int(n)]


def clean_basename(name):
    return _clean_basename(name)


def _clean_basename(name):
    if not name:
        return None
    name = str(name).replace("\\", "/").replace("\x00", "").strip()
    name = name.rstrip("/")
    if not name or name in (".", ".."):
        return None
    base = name.rsplit("/", 1)[-1].strip()
    if not base or base in (".", ".."):
        return None
    return base


def sanitize_source_path(path, basename=None):
    """Return a relative POSIX path with no `..` and no absolute root, or the basename, or None.

    Unsafe input (absolute paths that cannot be reduced, `..` components, NUL, UNC roots,
    Windows drive letters) is stripped down to a relative path or to `basename`.
    """
    fallback = _clean_basename(basename) or _clean_basename(path)
    if path is None:
        return fallback
    text = str(path).replace("\x00", "").replace("\\", "/").strip()
    if not text:
        return fallback
    # RFC 8089 / Windows: strip file://, drive letters, and UNC //server/share/
    if text.lower().startswith("file:"):
        text = re.sub(r"^[Ff][Ii][Ll][Ee]:/*", "", text)
    text = _DRIVE.sub("", text)
    if text.startswith("//"):
        text = _UNC.sub("", text)
    text = text.lstrip("/")
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return fallback
        parts.append(part)
    if not parts:
        return fallback
    rel = "/".join(parts)
    if len(rel) > MAX_SOURCE_PATH:
        rel = rel[-MAX_SOURCE_PATH:]
        if "/" in rel:
            rel = rel.split("/", 1)[-1]
    return rel or fallback


def relative_source_path(path, cwd=None, basename=None):
    """Best relative path to record for a CLI or filesystem input. Never absolute, never `..`."""
    base = basename or _clean_basename(path)
    if path is None or str(path).strip() == "":
        return sanitize_source_path(base, basename=base)
    raw = os.path.expanduser(str(path))
    cwd = os.path.abspath(cwd or os.getcwd())
    if not os.path.isabs(raw):
        return sanitize_source_path(raw, basename=base)
    try:
        rel = os.path.relpath(os.path.abspath(raw), cwd)
    except ValueError:
        rel = None
    if rel is None or rel == "." or rel.startswith(".." + os.sep) or rel == ".." or os.path.isabs(rel):
        parent = os.path.basename(os.path.dirname(os.path.abspath(raw)))
        name = os.path.basename(os.path.abspath(raw)) or base
        if parent and parent not in (os.sep, "", ".", ".."):
            return sanitize_source_path(f"{parent}/{name}", basename=name)
        return sanitize_source_path(name, basename=name)
    return sanitize_source_path(rel.replace("\\", "/"), basename=base)


def parent_dir(source_path):
    rel = sanitize_source_path(source_path)
    if not rel or "/" not in rel:
        return None
    parent = rel.rsplit("/", 1)[0]
    if not parent or parent in (".", ".."):
        return None
    return parent.rsplit("/", 1)[-1]


def attach_identity(container, source_path=None, original_filename=None):
    """Write `original_filename` and `source_path` onto a container dict (in place) and return it.

    Older readers ignore these keys. `original_filename` is always a basename. `source_path` is a
    sanitized relative path, omitted when it would be empty.
    """
    container = container if isinstance(container, dict) else {}
    base = _clean_basename(original_filename) or _clean_basename(source_path)
    rel = sanitize_source_path(source_path, basename=base)
    if base:
        container["original_filename"] = base
    if rel:
        container["source_path"] = rel
    return container


def identity_from_container(container):
    """Pull identity fields out of a container (missing on v1.0.0 files)."""
    container = container or {}
    qp = (container.get("quantum_parameters") or {}).get("qzip") or {}
    base = _clean_basename(container.get("original_filename")) or _clean_basename(qp.get("original_filename"))
    rel = sanitize_source_path(
        container.get("source_path") or qp.get("source_path"),
        basename=base,
    )
    sha = container.get("orig_sha256") or qp.get("orig_sha256")
    return {
        "original_filename": base,
        "source_path": rel,
        "orig_sha256": sha,
        "orig_sha256_short": short_sha(sha),
        "orig_size": container.get("orig_size") or qp.get("orig_size"),
    }


def _safe_component(text, fallback="file"):
    text = (text or "").replace("\x00", "").replace("/", "_").replace("\\", "_").strip()
    text = text.replace("\r", " ").replace("\n", " ")
    return text or fallback


def download_name(original_filename, source_path=None, orig_sha256=None, used_names=None, ext=".qz"):
    """Pick a Content-Disposition download name.

    Uses `{basename}{ext}` when that is unique in `used_names`. On a collision, prefers
    `{basename} ({parent}){ext}`, then `{basename}.{shortsha}{ext}`.
    """
    ext = ext or ""
    base = _safe_component(_clean_basename(original_filename) or _clean_basename(source_path) or "download")
    used = set(used_names or ())
    preferred = f"{base}{ext}"
    if preferred not in used:
        return preferred
    parent = parent_dir(source_path)
    if parent:
        with_parent = f"{base} ({_safe_component(parent)}){ext}"
        if with_parent not in used:
            return with_parent
        tagged = f"{base} ({_safe_component(parent)}).{short_sha(orig_sha256) or 'dup'}{ext}"
        if tagged not in used:
            return tagged
    sha = short_sha(orig_sha256) or "dup"
    candidate = f"{base}.{sha}{ext}"
    if candidate not in used:
        return candidate
    n = 2
    while True:
        extra = f"{base}.{sha}.{n}{ext}"
        if extra not in used:
            return extra
        n += 1


def ascii_fallback_filename(name):
    """ASCII filename= token for Content-Disposition (no quotes, backslashes, or controls)."""
    out = []
    for ch in str(name or ""):
        o = ord(ch)
        if ch in '"\\:\r\n/' or o < 32 or o == 127:
            out.append("_")
        elif o >= 128:
            out.append("_")
        else:
            out.append(ch)
    s = "".join(out).strip(" .") or "download"
    return s


def content_disposition(filename):
    """RFC 6266 / RFC 5987 Content-Disposition: ASCII filename plus UTF-8 filename*."""
    name = str(filename or "download").replace("\x00", "").replace("\r", "").replace("\n", "")
    fallback = ascii_fallback_filename(name)
    starred = "UTF-8''" + quote(name, safe="")
    return f'attachment; filename="{fallback}"; filename*={starred}'
