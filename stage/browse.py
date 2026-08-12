import difflib
import os

PREVIEW_CAP = 262_144


def resolve_within(root, rel_path):
    """Resolve a relative path against root; return the real path, or None when it escapes.

    Symbolic links are resolved before the containment check, so a link that
    points outside the root is rejected.
    """
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_real, rel_path.lstrip("/")))
    if candidate == root_real or candidate.startswith(root_real + os.sep):
        return candidate
    return None


def list_directory(path):
    """List a directory as dicts with name, is_dir, size, and mtime."""
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            stat = os.stat(full, follow_symlinks=False)
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "is_dir": os.path.isdir(full) and not os.path.islink(full),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    entries.sort(key=lambda e: (not e["is_dir"], e["name"]))
    return entries


def read_text_preview(path, cap=PREVIEW_CAP, tail=False):
    """Read up to cap bytes of a text file from the head or the tail.

    Returns content, truncated flag, total size, and a binary flag. Binary
    content (a NUL byte in the first 8 KiB) returns empty content.
    """
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        probe = f.read(8192)
        if b"\x00" in probe:
            return {"content": "", "truncated": False, "size": size, "binary": True}
        if tail and size > cap:
            f.seek(size - cap)
            data = f.read(cap)
        else:
            f.seek(0)
            data = f.read(cap)
    return {
        "content": data.decode("utf-8", errors="replace"),
        "truncated": size > cap,
        "size": size,
        "binary": False,
    }


def unified_diff_text(a_path, b_path, a_label, b_label):
    """Return a unified diff between two text files; empty when identical.

    Each input is read up to PREVIEW_CAP characters.
    """
    with open(a_path, "r", encoding="utf-8", errors="replace") as f:
        a_lines = f.read(PREVIEW_CAP).splitlines(keepends=True)
    with open(b_path, "r", encoding="utf-8", errors="replace") as f:
        b_lines = f.read(PREVIEW_CAP).splitlines(keepends=True)
    return "".join(difflib.unified_diff(a_lines, b_lines, fromfile=a_label, tofile=b_label))
