from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from backend.app.services import parsers
from backend.app.services.parsers import extract_text, property_type


def _pdf_with_text(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode("ascii"))
        content.extend(body)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(content)


def test_non_plaintext_documents_use_markitdown_local_conversion(tmp_path, monkeypatch):
    path = tmp_path / "product-guide.docx"
    path.write_bytes(b"binary-office-document")
    seen = []

    class Converter:
        def convert_local(self, supplied_path):
            seen.append(supplied_path)
            return SimpleNamespace(markdown="# Product Guide\n\nInstall Atlas first.")

    monkeypatch.setattr(parsers, "MarkItDown", lambda: Converter())
    parsers._document_converter.cache_clear()
    try:
        assert extract_text(path, "document") == (
            "# Product Guide\n\nInstall Atlas first."
        )
        assert seen == [path]
    finally:
        parsers._document_converter.cache_clear()


def test_plain_text_does_not_invoke_markitdown(tmp_path, monkeypatch):
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nPlain text content.", encoding="utf-8")

    class UnexpectedConverter:
        def __init__(self):
            raise AssertionError("plain text must not invoke MarkItDown")

    monkeypatch.setattr(parsers, "MarkItDown", UnexpectedConverter)
    parsers._document_converter.cache_clear()

    assert extract_text(path, "markdown") == "# Notes\n\nPlain text content."


def test_office_and_pdf_property_types_are_preserved():
    assert property_type("manual.docx") == "document"
    assert property_type("revenue.xlsx") == "spreadsheet"
    assert property_type("briefing.pptx") == "presentation"
    assert property_type("contract.pdf") == "pdf"


def test_images_remain_non_text_properties(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"not parsed")

    assert extract_text(path, "image") == ""


def test_real_docx_content_is_converted_to_markdown(tmp_path):
    path = tmp_path / "atlas-guide.docx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Atlas installation guide</w:t></w:r></w:p></w:body>
</w:document>""",
        )

    parsers._document_converter.cache_clear()
    try:
        assert "Atlas installation guide" in extract_text(path, "document")
    finally:
        parsers._document_converter.cache_clear()


def test_real_xlsx_content_is_converted_to_markdown(tmp_path):
    path = tmp_path / "atlas-revenue.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Product", "Revenue"])
    sheet.append(["Atlas", 125])
    workbook.save(path)

    parsers._document_converter.cache_clear()
    try:
        converted = extract_text(path, "spreadsheet")
    finally:
        parsers._document_converter.cache_clear()

    assert "Product" in converted
    assert "Atlas" in converted
    assert "125" in converted


def test_real_pdf_content_is_converted_to_markdown(tmp_path):
    path = tmp_path / "atlas-overview.pdf"
    path.write_bytes(_pdf_with_text("Atlas product overview"))

    parsers._document_converter.cache_clear()
    try:
        converted = extract_text(path, "pdf")
    finally:
        parsers._document_converter.cache_clear()

    assert "Atlas product overview" in converted
