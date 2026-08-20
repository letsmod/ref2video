"""Finding, probing and sanity-checking the local reference files.

Everything here runs before a single byte is uploaded, because every provider
rejects an oversized or over-long reference set only *after* the upload has
finished - which costs minutes and bandwidth for a request that was never going
to succeed.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

from .errors import R2VError

VIDEO_EXTENSIONS = {".mp4", ".mov"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def probe_duration_seconds(path: Path) -> float | None:
    """Read an mp4/mov clip's duration from its 'mvhd' header box.

    Pure stdlib box-walking so the tool keeps a single dependency. Returns None
    for anything it cannot parse; callers treat that as "unknown" and skip the
    limit check rather than blocking a file that might be fine.
    """
    def walk(handle, start, end, wanted):
        """Scan sibling boxes in [start, end) and return (body_start, box_end)."""
        offset = start
        while offset + 8 <= end:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                return None
            size = int.from_bytes(header[:4], "big")
            box_type = header[4:8]
            body_start = offset + 8
            if size == 1:  # 64-bit extended size follows the header
                size = int.from_bytes(handle.read(8), "big")
                body_start = offset + 16
            elif size == 0:  # box runs to the end of its parent
                size = end - offset
            box_end = min(offset + size, end)
            if box_end <= offset:
                return None
            if box_type == wanted:
                return body_start, box_end
            offset = box_end
        return None

    try:
        file_size = path.stat().st_size
        with path.open("rb") as handle:
            moov = walk(handle, 0, file_size, b"moov")
            if not moov:
                return None
            mvhd = walk(handle, moov[0], moov[1], b"mvhd")
            if not mvhd:
                return None

            handle.seek(mvhd[0])
            version = handle.read(1)[0]
            handle.read(3)  # flags
            if version == 1:
                handle.read(16)  # 64-bit creation + modification times
                timescale = int.from_bytes(handle.read(4), "big")
                duration = int.from_bytes(handle.read(8), "big")
            else:
                handle.read(8)  # 32-bit creation + modification times
                timescale = int.from_bytes(handle.read(4), "big")
                duration = int.from_bytes(handle.read(4), "big")
            if not timescale:
                return None
            return duration / timescale
    except (OSError, IndexError, ValueError):
        return None


def expand_reference_tokens(tokens: list[str], extensions: set[str], label: str) -> list[Path]:
    """Turn CLI arguments into a concrete, ordered list of reference files.

    Each token may be a plain path, a glob ('refs/*.mp4'), or a directory (every
    matching file inside it). Globs are expanded here rather than by the shell
    because PowerShell hands wildcards to scripts unexpanded, unlike bash.

    Ordering matters: position N in this list is what the prompt cites as
    reference N, so glob and directory matches are sorted by name to keep the
    numbering stable between runs.
    """
    paths: list[Path] = []
    for token in tokens:
        candidate = Path(token).expanduser()

        if any(ch in token for ch in "*?["):
            matches = sorted(Path(p) for p in glob.glob(os.path.expanduser(token), recursive=True))
            matches = [p for p in matches if p.is_file() and p.suffix.lower() in extensions]
            if not matches:
                raise R2VError(f"No {label} files matched the pattern: {token}")
            paths.extend(matches)
        elif candidate.is_dir():
            matches = sorted(p for p in candidate.iterdir()
                             if p.is_file() and p.suffix.lower() in extensions)
            if not matches:
                raise R2VError(
                    f"No {label} files ({', '.join(sorted(extensions))}) found in {candidate}"
                )
            paths.extend(matches)
        else:
            if not candidate.is_file():
                raise R2VError(f"Reference {label} not found: {candidate}")
            if candidate.suffix.lower() not in extensions:
                raise R2VError(
                    f"{candidate.name}: reference {label}s must be "
                    f"{' or '.join(sorted(extensions))}."
                )
            paths.append(candidate)

    # Drop duplicates (easy to hit when combining a directory and a named file)
    # while preserving the order that determines the reference numbering.
    seen, unique = set(), []
    for path in paths:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def total_reference_seconds(paths: list[Path]) -> float:
    """Combined duration of the clips whose length we could read."""
    return sum(probe_duration_seconds(p) or 0.0 for p in paths)


def check_clip_limits(paths: list[Path], min_s: float | None, max_s: float | None,
                      total_s: float | None) -> list[str]:
    """Return human-readable problems with the reference clip set.

    Clips we cannot probe are skipped rather than blocked - an unreadable header
    is not proof the file is bad, and the provider will have the final say.
    """
    problems: list[str] = []
    known: list[tuple[Path, float]] = []
    for path in paths:
        seconds = probe_duration_seconds(path)
        if seconds is None:
            continue
        known.append((path, seconds))
        if min_s is not None and seconds < min_s:
            problems.append(f"{path.name} is {seconds:.1f}s; clips must be at least {min_s}s.")
        if max_s is not None and seconds > max_s:
            problems.append(f"{path.name} is {seconds:.1f}s; clips must be at most {max_s}s.")

    if total_s is not None and known:
        total = sum(seconds for _, seconds in known)
        if total > total_s:
            problems.append(
                f"Reference clips total {total:.1f}s across {len(known)} files; this model "
                f"accepts {total_s}s combined (over by {total - total_s:.1f}s). Drop a clip, "
                f"trim one, or pass --drop-excess-refs to let the tool choose."
            )
    return problems


def fit_reference_clips(paths: list[Path], total_s: float) -> tuple[list[Path], list[Path]]:
    """Greedily keep clips, in the given order, that fit the combined-duration cap.

    Returns (kept, dropped). A clip that would push the total over is skipped and
    later, shorter clips still get a chance, so we keep as many angles as possible.
    """
    kept: list[Path] = []
    dropped: list[Path] = []
    total = 0.0
    for path in paths:
        seconds = probe_duration_seconds(path) or 0.0
        if kept and total + seconds > total_s:
            dropped.append(path)
            continue
        kept.append(path)
        total += seconds
    return kept, dropped


def check_file_size(path: Path, max_mb: float | None) -> None:
    """Raise if a reference file is bigger than the provider will accept."""
    if max_mb is None:
        return
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        raise R2VError(f"{path.name} is {size_mb:.1f} MB; the limit here is {max_mb} MB.")
