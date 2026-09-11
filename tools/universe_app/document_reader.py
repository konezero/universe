"""Read only registered project documents, bounded to their project root."""
from pathlib import Path


class DocumentReadError(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def read_document(project, projection, document_id):
    document = next((item for item in projection.get("documents", []) if item.get("document_id") == document_id), None)
    if document is None:
        raise DocumentReadError("DOCUMENT_NOT_FOUND", "Document is not registered in this project")
    root = Path(project["project_root"]).resolve()
    relative = Path(document.get("path") or "")
    target = (root / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(root):
        raise DocumentReadError("DOCUMENT_PATH_INVALID", "Document must remain inside its project")
    if target.suffix.lower() not in {".md", ".txt", ".html", ".htm", ".rst"}:
        raise DocumentReadError("DOCUMENT_FORMAT_UNSUPPORTED", "This document format cannot be previewed")
    try:
        with target.open("rb") as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise DocumentReadError("DOCUMENT_TOO_LARGE", "Document exceeds the 2 MiB preview limit")
        content = data.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise DocumentReadError("DOCUMENT_READ_FAILED", "Document is missing, unreadable, or not UTF-8") from error
    return {**document, "content": content, "format": "html" if target.suffix.lower() in {".html", ".htm"} else "markdown"}
