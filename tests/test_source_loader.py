"""test_source_loader.py - Phase 1: dual-entry source ingestion.

Covers 5 input types + paste + dispatcher. PDF and DOCX loaders are
tested with mocked library modules so the suite passes regardless of
whether pypdf/python-docx are installed (the loader's own try/except
ImportError path is covered by a separate test).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.source_loader import (
    SUPPORTED_EXTENSIONS,
    SourceDocument,
    SourceLoaderError,
    _detect_language,
    _is_url,
    _normalize_text,
    _strip_md_frontmatter,
    load_source,
)


# ── Helpers ─────────────────────────────────────────────────────────
def _write(path: Path, content: str | bytes, binary: bool = False) -> Path:
    if binary or isinstance(content, bytes):
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    else:
        path.write_text(content, encoding="utf-8", newline="")
    return path


# ── Pure-function unit tests (no I/O) ───────────────────────────────
class TestHelpers:
    def test_normalize_strips_bom(self):
        assert _normalize_text("\ufeffhello") == "hello"

    def test_normalize_crlf_to_lf(self):
        assert _normalize_text("a\r\nb\rc\nd") == "a\nb\nc\nd"

    def test_normalize_preserves_text(self):
        assert _normalize_text("plain text") == "plain text"

    def test_is_url_https(self):
        assert _is_url("https://example.com")

    def test_is_url_http(self):
        assert _is_url("http://example.com")

    def test_is_url_paste(self):
        assert not _is_url("hello world")

    def test_is_url_path(self):
        assert not _is_url("C:/file.txt")

    def test_strip_md_frontmatter_full(self):
        text = "---\ntitle: Foo\nauthor: Bar\n---\n# Body\nhello"
        body, meta = _strip_md_frontmatter(text)
        assert body == "# Body\nhello"
        assert meta == {"title": "Foo", "author": "Bar"}

    def test_strip_md_frontmatter_none(self):
        text = "# Just content\nno front matter"
        body, meta = _strip_md_frontmatter(text)
        assert body == text
        assert meta == {}

    def test_strip_md_frontmatter_unclosed(self):
        text = "---\ntitle: Foo\nbody without closing"
        body, meta = _strip_md_frontmatter(text)
        assert body == text
        assert meta == {}

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("hello world", "en"),
            ("नमस्ते दोस्त", "hi"),
            ("नमस्ते hello friend", "mixed"),
            ("", "unknown"),
            ("12345 !!!", "unknown"),
            ("!@#$%^&*()", "unknown"),
        ],
    )
    def test_detect_language(self, text, expected):
        assert _detect_language(text) == expected


# ── TXT loader ──────────────────────────────────────────────────────
class TestTxtLoader:
    def test_basic(self, tmp_path):
        p = _write(tmp_path / "a.txt", "hello world\n", binary=True)
        doc = load_source(p)
        assert doc.source_type == "txt"
        assert doc.text == "hello world\n"
        assert doc.word_count == 2
        assert doc.language == "en"
        assert doc.metadata["size_bytes"] == p.stat().st_size

    def test_bom_stripped(self, tmp_path):
        p = _write(tmp_path / "a.txt", "\ufeffhello world")
        doc = load_source(p)
        assert doc.text == "hello world"

    def test_crlf_normalized(self, tmp_path):
        p = _write(tmp_path / "a.txt", "line1\r\nline2\r\nline3", binary=True)
        doc = load_source(p)
        assert "\r" not in doc.text
        assert doc.text == "line1\nline2\nline3"

    def test_empty_file(self, tmp_path):
        p = _write(tmp_path / "empty.txt", "")
        doc = load_source(p)
        assert doc.text == ""
        assert doc.word_count == 0
        assert doc.language == "unknown"

    def test_devanagari_detected(self, tmp_path):
        p = _write(tmp_path / "hi.txt", "नमस्ते दोस्त कैसे हो")
        doc = load_source(p)
        assert doc.language == "hi"
        assert doc.word_count == 4

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(SourceLoaderError, match="File not found"):
            load_source(tmp_path / "nope.txt")

    def test_unsupported_extension_raises(self, tmp_path):
        p = _write(tmp_path / "data.xyz", "x")
        with pytest.raises(SourceLoaderError, match="Unsupported file extension"):
            load_source(p)

    def test_string_path_with_txt_extension(self, tmp_path):
        p = _write(tmp_path / "a.txt", "ok")
        doc = load_source(str(p))
        assert doc.source_type == "txt"


# ── MD loader ───────────────────────────────────────────────────────
class TestMdLoader:
    def test_basic(self, tmp_path):
        p = _write(tmp_path / "a.md", "# Hello\n\nworld")
        doc = load_source(p)
        assert doc.source_type == "md"
        assert "# Hello" in doc.text

    def test_frontmatter_stripped(self, tmp_path):
        p = _write(
            tmp_path / "a.md",
            "---\ntitle: Story\nauthor: Alice\n---\n# Body\ncontent here",
        )
        doc = load_source(p)
        assert doc.source_type == "md"
        assert doc.text.startswith("# Body")
        assert "title" not in doc.text.lower().split("\n")[0]
        assert doc.metadata["front_matter"] == {"title": "Story", "author": "Alice"}

    def test_no_frontmatter(self, tmp_path):
        p = _write(tmp_path / "a.md", "# Just a title\nbody text")
        doc = load_source(p)
        assert doc.metadata["front_matter"] == {}

    def test_empty_md(self, tmp_path):
        p = _write(tmp_path / "a.md", "")
        doc = load_source(p)
        assert doc.text == ""
        assert doc.word_count == 0


# ── Paste loader ────────────────────────────────────────────────────
class TestPasteLoader:
    def test_plain_string_treated_as_paste(self):
        doc = load_source("just a regular string, no path-like ending")
        assert doc.source_type == "paste"
        assert doc.text == "just a regular string, no path-like ending"

    def test_paste_with_bom(self):
        doc = load_source("\ufeffनमस्ते")
        assert doc.text == "नमस्ते"
        assert doc.source_type == "paste"

    def test_paste_empty(self):
        doc = load_source("")
        assert doc.text == ""
        assert doc.word_count == 0

    def test_paste_long_devanagari(self):
        long_text = " ".join(["नमस्ते"] * 100)
        doc = load_source(long_text)
        assert doc.word_count == 100
        assert doc.language == "hi"


# ── URL loader (with mocked requests + trafilatura) ─────────────────
class TestUrlLoader:
    def _mock_traf(self, extract_return: str | None):
        mock_mod = MagicMock()
        mock_mod.extract.return_value = extract_return
        return mock_mod

    def test_fetch_and_extract(self):
        html = "<html><body><article>Main content here with real text.</article><nav>ignore nav</nav></body></html>"
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/redirected"
        with (
            patch("requests.get", return_value=mock_resp),
            patch.dict(
                "sys.modules", {"trafilatura": self._mock_traf("Main content here with real text.")}
            ),
        ):
            doc = load_source("https://example.com/page")
        assert doc.source_type == "url"
        assert "Main content" in doc.text
        assert doc.metadata["url"] == "https://example.com/page"
        assert doc.metadata["final_url"] == "https://example.com/redirected"
        assert doc.metadata["status_code"] == 200

    def test_fetch_404_raises(self):
        import requests

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        with (
            patch("requests.get", return_value=mock_resp),
            patch.dict("sys.modules", {"trafilatura": self._mock_traf("x")}),
        ):
            with pytest.raises(SourceLoaderError, match="URL fetch failed"):
                load_source("https://example.com/missing")

    def test_no_main_content_raises(self):
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><script>var x=1;</script></body></html>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        with (
            patch("requests.get", return_value=mock_resp),
            patch.dict("sys.modules", {"trafilatura": self._mock_traf("")}),
        ):
            with pytest.raises(SourceLoaderError, match="no main-content text"):
                load_source("https://example.com")

    def test_user_agent_passed(self):
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>content text here for extraction</body></html>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com"
        with (
            patch("requests.get", return_value=mock_resp) as mock_get,
            patch.dict(
                "sys.modules", {"trafilatura": self._mock_traf("content text here for extraction")}
            ),
        ):
            load_source(
                "https://example.com",
                config={"source": {"user_agent": "TestAgent/1.0", "url_timeout_s": 5}},
            )
        kwargs = mock_get.call_args.kwargs
        assert kwargs["headers"]["User-Agent"] == "TestAgent/1.0"
        assert kwargs["timeout"] == 5

    def test_oversize_url_text_warns(self, caplog):
        import logging

        big_text = "word " * 60000
        mock_resp = MagicMock()
        mock_resp.text = f"<html><body>{big_text}</body></html>"
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/big"
        with (
            caplog.at_level(logging.WARNING),
            patch("requests.get", return_value=mock_resp),
            patch.dict("sys.modules", {"trafilatura": self._mock_traf(big_text)}),
        ):
            doc = load_source("https://example.com/big")
        assert doc.word_count > 50000
        assert any("soft cap" in r.message for r in caplog.records)


# ── PDF loader (with mocked pypdf) ─────────────────────────────────
class TestPdfLoader:
    def _mock_pypdf(self, page_texts):
        mock_reader = MagicMock()
        mock_reader.pages = []
        for text in page_texts:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = text
            mock_reader.pages.append(mock_page)
        mock_module = MagicMock()
        mock_module.PdfReader.return_value = mock_reader
        return mock_module

    def test_basic_pdf(self, tmp_path):
        p = _write(tmp_path / "a.pdf", b"%PDF-fake")
        mock_pypdf = self._mock_pypdf(["Page one content.", "Page two content."])
        with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
            doc = load_source(p)
        assert doc.source_type == "pdf"
        assert "Page one" in doc.text
        assert "Page two" in doc.text
        assert doc.metadata["page_count"] == 2

    def test_scanned_pdf_raises(self, tmp_path):
        p = _write(tmp_path / "scan.pdf", b"%PDF-fake")
        mock_pypdf = self._mock_pypdf(["", "", ""])
        with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
            with pytest.raises(SourceLoaderError, match="no extractable text"):
                load_source(p)

    def test_missing_pypdf_raises(self, tmp_path):
        p = _write(tmp_path / "a.pdf", b"%PDF-fake")
        with patch.dict("sys.modules", {"pypdf": None}):
            with pytest.raises(SourceLoaderError, match="pypdf is required"):
                load_source(p)

    def test_page_extraction_failure_continues(self, tmp_path):
        p = _write(tmp_path / "a.pdf", b"%PDF-fake")
        mock_reader = MagicMock()
        p1 = MagicMock()
        p1.extract_text.side_effect = RuntimeError("corrupt page")
        p2 = MagicMock()
        p2.extract_text.return_value = "OK page"
        mock_reader.pages = [p1, p2]
        mock_module = MagicMock()
        mock_module.PdfReader.return_value = mock_reader
        with patch.dict("sys.modules", {"pypdf": mock_module}):
            doc = load_source(p)
        assert "OK page" in doc.text


# ── DOCX loader (with mocked python-docx) ──────────────────────────
class TestDocxLoader:
    def _mock_docx(self, paragraphs, author="Alice"):
        mock_para_objs = []
        for text, style_name in paragraphs:
            p = MagicMock()
            p.text = text
            p.style.name = style_name
            mock_para_objs.append(p)
        mock_doc = MagicMock()
        mock_doc.paragraphs = mock_para_objs
        mock_doc.core_properties.author = author
        mock_module = MagicMock()
        mock_module.Document.return_value = mock_doc
        return mock_module

    def test_basic_docx(self, tmp_path):
        p = _write(tmp_path / "a.docx", b"PK-fake")
        mock_docx = self._mock_docx(
            [
                ("Title text", "Heading 1"),
                ("Body text.", "Normal"),
            ]
        )
        with patch.dict("sys.modules", {"docx": mock_docx}):
            doc = load_source(p)
        assert doc.source_type == "docx"
        assert "Title text" in doc.text
        assert "Body text." in doc.text
        assert doc.metadata["headings"] == [{"style": "Heading 1", "text": "Title text"}]
        assert doc.metadata["author"] == "Alice"

    def test_missing_python_docx_raises(self, tmp_path):
        p = _write(tmp_path / "a.docx", b"PK-fake")
        with patch.dict("sys.modules", {"docx": None}):
            with pytest.raises(SourceLoaderError, match="python-docx is required"):
                load_source(p)

    def test_multiple_headings_preserved(self, tmp_path):
        p = _write(tmp_path / "a.docx", b"PK-fake")
        mock_docx = self._mock_docx(
            [
                ("Chapter 1", "Heading 1"),
                ("para", "Normal"),
                ("Section A", "Heading 2"),
                ("more para", "Normal"),
            ]
        )
        with patch.dict("sys.modules", {"docx": mock_docx}):
            doc = load_source(p)
        assert len(doc.metadata["headings"]) == 2
        assert doc.metadata["headings"][1]["text"] == "Section A"


# ── Dispatcher & integration ────────────────────────────────────────
class TestDispatcher:
    def test_path_dispatches_to_correct_loader(self, tmp_path):
        p = _write(tmp_path / "a.txt", "hi")
        doc = load_source(p)
        assert doc.source_type == "txt"

    def test_string_url_dispatches_to_url(self):
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>main article content here</body></html>"
        mock_resp.status_code = 200
        mock_resp.url = "https://x.com"
        mock_traf = MagicMock()
        mock_traf.extract.return_value = "main article content here"
        with (
            patch("requests.get", return_value=mock_resp),
            patch.dict("sys.modules", {"trafilatura": mock_traf}),
        ):
            doc = load_source("https://x.com")
        assert doc.source_type == "url"

    def test_plain_string_dispatches_to_paste(self):
        doc = load_source("not a url and not a path")
        assert doc.source_type == "paste"

    def test_unsupported_type_raises(self):
        with pytest.raises(SourceLoaderError, match="Unsupported source type"):
            load_source(12345)

    def test_oversize_soft_cap_warns(self, tmp_path, caplog):
        import logging

        big = "x " * 60000
        p = _write(tmp_path / "big.txt", big)
        with caplog.at_level(logging.WARNING):
            doc = load_source(p)
        assert doc.word_count > 50000
        assert any("soft cap" in r.message for r in caplog.records)

    def test_allowed_extensions_from_config(self, tmp_path):
        cfg = {"source": {"allowed_extensions": [".txt"], "max_words": 50000}}
        p = _write(tmp_path / "a.md", "# hi")
        with pytest.raises(SourceLoaderError, match="Unsupported file extension"):
            load_source(p, config=cfg)

    def test_source_document_is_dataclass(self):
        d = SourceDocument(text="x", word_count=1, language="en", source_type="paste")
        assert d.text == "x"
        assert d.metadata == {}

    def test_supported_extensions_constant(self):
        assert ".txt" in SUPPORTED_EXTENSIONS
        assert ".md" in SUPPORTED_EXTENSIONS
        assert ".pdf" in SUPPORTED_EXTENSIONS
        assert ".docx" in SUPPORTED_EXTENSIONS


# ── End-to-end smoke test ──────────────────────────────────────────
class TestEndToEnd:
    def test_full_flow_txt_then_paste(self, tmp_path):
        p = _write(tmp_path / "story.txt", "Once upon a time.\n\nThe end.")
        d1 = load_source(p)
        d2 = load_source(d1.text)
        assert d1.text == d2.text
        assert d1.word_count == d2.word_count
