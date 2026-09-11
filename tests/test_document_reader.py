import tempfile
import unittest
from pathlib import Path
from tools.universe_app.document_reader import read_document, DocumentReadError


class DocumentReaderTests(unittest.TestCase):
    def test_registered_content_and_project_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "readme.md").write_text("# Hello", encoding="utf-8")
            project = {"project_root": directory}
            projection = {"documents": [{"document_id": "readme", "path": "readme.md"}]}
            self.assertEqual(read_document(project, projection, "readme")["content"], "# Hello")
            with self.assertRaises(DocumentReadError) as caught:
                read_document(project, projection, "other-project-doc")
            self.assertEqual(caught.exception.code, "DOCUMENT_NOT_FOUND")
            projection["documents"][0]["path"] = "../outside.md"
            with self.assertRaises(DocumentReadError) as caught:
                read_document(project, projection, "readme")
            self.assertEqual(caught.exception.code, "DOCUMENT_PATH_INVALID")

    def test_limits_missing_and_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = {"project_root": directory}
            projection = {"documents": [{"document_id": "doc", "path": "doc.html"}]}
            with self.assertRaises(DocumentReadError) as caught:
                read_document(project, projection, "doc")
            self.assertEqual(caught.exception.code, "DOCUMENT_READ_FAILED")
            (root / "doc.html").write_text("<h1>Hello</h1>", encoding="utf-8")
            self.assertEqual(read_document(project, projection, "doc")["format"], "html")
            (root / "doc.html").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            with self.assertRaises(DocumentReadError) as caught:
                read_document(project, projection, "doc")
            self.assertEqual(caught.exception.code, "DOCUMENT_TOO_LARGE")
