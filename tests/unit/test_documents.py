import pytest

from app.workshop.documents import DocumentError, extract_text


def test_text_files_are_kept_as_written():
    assert extract_text("a.md", "# Títol\r\n\r\nText.\r\n".encode()) == "# Títol\n\nText."
    assert extract_text("a.txt", "Caf\xe9".encode("cp1252")) == "Café"


def test_converted_files_go_through_markitdown():
    html = "<html><body><h1>Fotosíntesi</h1><p>Passa als <b>cloroplasts</b>.</p></body></html>"
    text = extract_text("apunts.html", html.encode())
    assert "# Fotosíntesi" in text and "**cloroplasts**" in text


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF with `text` in Helvetica (ASCII only)."""
    stream = b"BT /F1 18 Tf 20 100 Td (" + text.encode() + b") Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_pdf_text_is_extracted():
    assert extract_text("apunts.pdf", _minimal_pdf("Hola mon, la fotosintesi")) == \
        "Hola mon, la fotosintesi"


def test_a_broken_pdf_is_reported_not_raised():
    garbage = b"%PDF-1.7\n" + bytes((i * 7919) % 256 for i in range(4000))
    with pytest.raises(DocumentError) as e:
        extract_text("trencat.pdf", garbage)
    assert e.value.code == "unreadable"
