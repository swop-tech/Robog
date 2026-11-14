"""Path-traversal-safe serving of the manuals' figures (``GET /media/...``).

476 figure chunks carry an ``image`` path (``images/de-p016L-fig01.png``) that
resolves on disk, and until now nothing could reach it over HTTP (gap G6
display, ``docs/pipeline-review.md`` §9a). This module is the resolver behind
the media endpoint: it answers two questions and refuses everything else.

1. *Which ``images/`` folder does this ``doc_id`` belong to?* -- answered from
   the chunk index (``doc_id`` -> ``source_file``'s folder) and cross-checked
   against the filesystem, so a ``doc_id`` nobody ingested cannot be served.
2. *Is this filename a PNG inside that folder?* -- every request is checked
   four times over before a byte is read: no separators or ``..`` in the name,
   the resolved path stays inside the document's ``images/`` folder, it stays
   inside the data root, and its suffix is in ``ALLOWED_SUFFIXES``.

Nothing here knows about FastAPI, so it is testable (and reusable) on its own::

    store = MediaStore.build(Path("data"))
    store.url_for("flashbot_user_manual_v1_0-multilanguage-de", "images/de-p018L-fig01.png")
    store.resolve("flashbot_user_manual_v1_0-multilanguage-de", "de-p018L-fig01.png")
    store.resolve("...", "../../../.env")     # -> None, never a path
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from ingest.chunk import doc_id_for

#: The figures in ``data/**/images/`` are PNGs; nothing else is ever served.
ALLOWED_SUFFIXES: frozenset[str] = frozenset({".png"})
IMAGES_DIRNAME = "images"
#: Language files of a manual folder -- their stems are the folder's languages.
MANUAL_LANG_FILES = ("en.md", "de.md", "it.md")

# Characters that must never appear in a filename we serve. Checking them on
# the *decoded* name is what makes ``..%2f`` and ``%2e%2e/`` ordinary 404s.
_FORBIDDEN = ("/", "\\", "\x00")


def safe_filename(filename: str) -> str | None:
    """Return ``filename`` when it is a single, plain path component.

    Rejects traversal (``..``, ``../x``, ``a/../b``), absolute paths, Windows
    separators, NUL bytes and empty names -- the caller then 404s instead of
    reading anything.
    """
    name = (filename or "").strip()
    if not name:
        return None
    if any(bad in name for bad in _FORBIDDEN):
        return None
    if name in (".", ".."):
        return None
    if ".." in PurePosixPath(name).parts:
        return None
    # A leading dot would allow hidden files; the dataset has none.
    if name.startswith("."):
        return None
    return name


def is_within(path: Path, root: Path) -> bool:
    """True when the resolved ``path`` is ``root`` itself or lives under it."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass
class MediaStore:
    """``doc_id`` -> that document's ``images/`` folder, and safe resolution."""

    data_root: Path
    images_dirs: dict[str, Path] = field(default_factory=dict)
    allowed_suffixes: frozenset[str] = ALLOWED_SUFFIXES

    # -- construction ------------------------------------------------------
    @classmethod
    def build(
        cls,
        data_root: str | Path = "data",
        chunks: list[dict] | None = None,
    ) -> "MediaStore":
        """Index the figures on disk, then refine with the chunk index.

        The filesystem walk makes the endpoint work before/without a built
        index; the chunk metadata is authoritative about which ``doc_id`` owns
        a folder and wins when both know it.
        """
        store = cls(data_root=Path(data_root).resolve())
        store.scan_filesystem()
        if chunks:
            store.register_chunks(chunks)
        return store

    def scan_filesystem(self) -> int:
        """Find every ``data/**/images/`` folder and the languages next to it."""
        found = 0
        if not self.data_root.is_dir():
            return 0
        for images_dir in sorted(self.data_root.rglob(IMAGES_DIRNAME)):
            if not images_dir.is_dir():
                continue
            manual_dir = images_dir.parent
            for md_file in sorted(manual_dir.glob("*.md")):
                if md_file.name not in MANUAL_LANG_FILES:
                    continue
                self.register(doc_id_for(manual_dir, md_file.stem), images_dir)
                found += 1
        return found

    def register_chunks(self, chunks: list[dict]) -> int:
        """Map each chunk's ``doc_id`` to its manual's ``images/`` folder."""
        added = 0
        for chunk in chunks:
            meta = chunk.get("metadata", {})
            doc_id = meta.get("doc_id")
            source = meta.get("source_file")
            if not doc_id or not source:
                continue
            images_dir = Path(source).parent / IMAGES_DIRNAME
            if not images_dir.is_dir():
                continue
            if self.images_dirs.get(doc_id) != images_dir:
                added += 1
            self.register(doc_id, images_dir)
        return added

    def register(self, doc_id: str, images_dir: str | Path) -> None:
        doc_id = (doc_id or "").strip()
        if not doc_id:
            return
        self.images_dirs[doc_id] = Path(images_dir)

    # -- lookup ------------------------------------------------------------
    @property
    def doc_ids(self) -> list[str]:
        return sorted(self.images_dirs)

    def __len__(self) -> int:
        return len(self.images_dirs)

    def images_dir_for(self, doc_id: str) -> Path | None:
        return self.images_dirs.get((doc_id or "").strip())

    def files_for(self, doc_id: str) -> list[str]:
        """PNG filenames a document can actually serve (for health/listing).

        Same rules as ``resolve()`` -- a symlink pointing out of the folder is
        not servable, so it is not listed either.
        """
        images_dir = self.images_dir_for(doc_id)
        if images_dir is None:
            return []
        return self._servable_names(images_dir)

    def _servable_names(self, images_dir: Path) -> list[str]:
        if not images_dir.is_dir():
            return []
        try:
            root = images_dir.resolve()
        except OSError:  # pragma: no cover - unreadable directory
            return []
        names = []
        for path in sorted(images_dir.iterdir()):
            if path.suffix.lower() not in self.allowed_suffixes or not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:  # pragma: no cover - broken symlink
                continue
            if is_within(resolved, root):
                names.append(path.name)
        return names

    @property
    def unique_dirs(self) -> list[Path]:
        """Distinct ``images/`` folders -- one folder serves all its languages."""
        return list(dict.fromkeys(self.images_dirs.values()))

    def file_count(self) -> int:
        """Distinct servable PNGs on disk (a folder is not counted per language)."""
        return sum(len(self._servable_names(images_dir)) for images_dir in self.unique_dirs)

    def resolve(self, doc_id: str, filename: str) -> Path | None:
        """The file to serve, or ``None`` when the request is not servable.

        Four independent checks; any failure returns ``None`` (-> HTTP 404),
        never an exception and never a path outside the data root.
        """
        images_dir = self.images_dir_for(doc_id)
        if images_dir is None:
            return None
        name = safe_filename(filename)
        if name is None:
            return None
        try:
            images_root = images_dir.resolve()
            candidate = (images_dir / name).resolve()
        except (OSError, ValueError):
            return None
        if not is_within(candidate, images_root):
            return None
        if not is_within(candidate, self.data_root):
            return None
        if candidate.suffix.lower() not in self.allowed_suffixes:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def url_for(
        self,
        doc_id: str | None,
        image: str | None,
        *,
        prefix: str = "/media",
        base: str = "",
    ) -> str | None:
        """An HTTP URL for a chunk/profile ``image`` path, or ``None``.

        ``image`` is what the corpus stores (``images/de-p018L-fig01.png``);
        the URL keeps the bare ``doc_id`` + filename shape of the endpoint.
        ``base`` (e.g. the request's origin) makes the URL absolute, as the
        chat UI needs; without it the URL stays root-relative.
        """
        if not doc_id or not image:
            return None
        name = safe_filename(PurePosixPath(str(image)).name)
        if name is None or self.resolve(doc_id, name) is None:
            return None
        path = f"{prefix.rstrip('/')}/{quote(str(doc_id))}/{quote(name)}"
        if base:
            return f"{base.rstrip('/')}{path}"
        return path
