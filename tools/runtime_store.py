"""Machine-wide content-addressed blob store for the LINKED runtime install.

One store per machine. A project's managed ``.ai/**`` files become symlinks
into ``objects/sha256/<content-sha256>``.  Blobs are written read-only, so an
edit through a project symlink fails loudly - that is the ownership boundary:
change the canonical source and rebuild a release, never the installed copy.

This is the persistent form of the content-addressed store that
``ReleaseRuntime.materialize_source_bundle`` already builds as a throwaway
bundle.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

_SHA256_HEX = 64
_HEX = frozenset("0123456789abcdef")
_ENV_STORE_ROOT = "UNIVERSE_RUNTIME_STORE"


class RuntimeStoreError(RuntimeError):
    pass


def default_store_root() -> Path:
    """Machine store root: ``<LOCALAPPDATA>/Universe/runtime-store`` (the same
    ``~/AppData/Local/Universe/`` tree that holds ``server.json`` etc.),
    overridable via ``UNIVERSE_RUNTIME_STORE``."""

    override = os.environ.get(_ENV_STORE_ROOT)
    if override and override.strip():
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "Universe" / "runtime-store"


def _validated_sha(sha256: str) -> str:
    value = str(sha256).strip().lower()
    if len(value) != _SHA256_HEX or any(character not in _HEX for character in value):
        raise RuntimeStoreError(f"invalid content digest: {sha256!r}")
    return value


def blob_path(store_root: Path, sha256: str) -> Path:
    return Path(store_root) / "objects" / "sha256" / _validated_sha(sha256)


def ensure_blob(store_root: Path, sha256: str, content: bytes) -> Path:
    """Idempotently place ``content`` in the store as a read-only object.

    Verifies the declared digest against the bytes, and against any object
    already present (raises on mismatch rather than trusting the store).
    """

    digest = _validated_sha(sha256)
    if hashlib.sha256(content).hexdigest() != digest:
        raise RuntimeStoreError("content does not match its declared digest")
    target = blob_path(store_root, digest)
    if target.is_symlink() or target.exists():
        if not target.is_file() or target.is_symlink():
            raise RuntimeStoreError(f"store object is not a regular file: {target}")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeStoreError(f"store object is corrupt: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{digest}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, target)
    finally:
        if temporary.is_symlink() or temporary.exists():
            os.chmod(temporary, 0o600)
            temporary.unlink()
    return target


def store_symlink_sha(link_path: Path, store_root: Path) -> str | None:
    """If ``link_path`` is a symlink whose target is
    ``<store_root>/objects/sha256/<64hex>``, return that sha, else ``None``.

    Reads the link text directly (no filesystem resolve), so a dangling link
    into the store is still recognised as ours.
    """

    link = Path(link_path)
    if not link.is_symlink():
        return None
    try:
        raw = os.readlink(link)
    except OSError:
        return None
    # Windows returns the extended-length form ("\\?\C:\..." / "\\?\UNC\...")
    # for an absolute symlink target; normalise it back to a plain path.
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[len("\\\\?\\UNC\\"):]
    elif raw.startswith("\\\\?\\"):
        raw = raw[len("\\\\?\\"):]
    target = Path(raw)
    if not target.is_absolute():
        target = link.parent / target
    normalized = os.path.normcase(os.path.normpath(str(target)))
    prefix = (
        os.path.normcase(
            os.path.normpath(str(Path(store_root) / "objects" / "sha256"))
        )
        + os.sep
    )
    if not normalized.startswith(prefix):
        return None
    name = normalized[len(prefix):]
    if os.sep in name or (os.altsep and os.altsep in name):
        return None
    if len(name) == _SHA256_HEX and all(character in _HEX for character in name):
        return name
    return None


def sweep_unreferenced(store_root: Path, *, keep: set[str]) -> list[str]:
    """Remove store objects whose sha is not in ``keep``.  Returns the shas
    removed.  Only touches ``objects/sha256/*`` regular files."""

    kept = {_validated_sha(value) for value in keep}
    objects_dir = Path(store_root) / "objects" / "sha256"
    if not objects_dir.is_dir():
        return []
    removed: list[str] = []
    for child in sorted(objects_dir.iterdir()):
        if not child.is_file() or child.is_symlink():
            continue
        if child.name in kept:
            continue
        os.chmod(child, 0o600)
        child.unlink()
        removed.append(child.name)
    return removed
