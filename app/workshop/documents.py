"""Step 2: turn an uploaded file into the text that goes into the context.

The document is placed in the context whole (plan T12), so only its text is
kept: plain text and Markdown as they are, PDF / Word / PowerPoint / HTML
through markitdown. The file itself is not stored."""

import io
import os

MAX_BYTES = 10 * 1024 * 1024  # the old branch's limit

TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
CONVERTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm"}
ACCEPTED = sorted(TEXT_EXTENSIONS | CONVERTED_EXTENSIONS)


class DocumentError(ValueError):
    """The upload cannot become context text. `code` is stable for the UI."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def extract_text(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if len(data) > MAX_BYTES:
        raise DocumentError("too_large", f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if ext in TEXT_EXTENSIONS:
        text = _decode(data)
    elif ext in CONVERTED_EXTENSIONS:
        text = _convert(data, ext, filename)
    else:
        raise DocumentError("unsupported", "This file type is not supported. Use "
                            + ", ".join(ACCEPTED) + ".")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise DocumentError("empty", "No text could be extracted from this file "
                            "(a scanned PDF has images, not text).")
    return text


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _convert(data: bytes, ext: str, filename: str) -> str:
    from markitdown import MarkItDown, StreamInfo  # heavy import, only when needed

    try:
        result = MarkItDown(enable_plugins=False).convert_stream(
            io.BytesIO(data), stream_info=StreamInfo(extension=ext, filename=filename))
    except Exception as e:  # markitdown raises many kinds for broken files
        raise DocumentError("unreadable", "The file could not be read. Is it damaged?") from e
    return result.text_content or ""
