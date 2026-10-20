"""`PypdfTextReader`: in-memory PDF text extraction with pypdf (no temporary files, no OCR).

Rejects, with a specific code, anything it cannot read reliably: non-PDF bytes, malformed or
encrypted files, too many pages or too much text, and image-only (scanned) documents. The
MVP deliberately has no OCR fallback: a scanned payslip gets a helpful error instead of
guessed values.
"""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.ai.application.payslip.errors import PayslipError, PayslipErrorCode
from app.ai.application.payslip.models import DocumentText
from app.ai.application.payslip.ports import PayslipImportConfig

PDF_MAGIC = b"%PDF-"
_HEADER_WINDOW = 1024


class PypdfTextReader:
    def read(self, data: bytes, config: PayslipImportConfig) -> DocumentText:
        if not data:
            raise PayslipError(PayslipErrorCode.EMPTY_FILE)
        if len(data) > config.max_bytes:
            raise PayslipError(PayslipErrorCode.FILE_TOO_LARGE)
        if PDF_MAGIC not in data[:_HEADER_WINDOW]:
            raise PayslipError(PayslipErrorCode.NOT_A_PDF)
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise PayslipError(PayslipErrorCode.ENCRYPTED_PDF)
            page_count = len(reader.pages)
            if page_count == 0:
                raise PayslipError(PayslipErrorCode.MALFORMED_PDF)
            if page_count > config.max_pages:
                raise PayslipError(PayslipErrorCode.TOO_MANY_PAGES)
            pages: list[tuple[str, ...]] = []
            visible = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                visible += sum(1 for ch in text if not ch.isspace())
                if visible > config.max_text_chars:
                    raise PayslipError(PayslipErrorCode.TOO_MUCH_TEXT)
                pages.append(tuple(text.splitlines()))
        except PayslipError:
            raise
        except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            raise PayslipError(PayslipErrorCode.MALFORMED_PDF) from None
        except RecursionError:
            raise PayslipError(PayslipErrorCode.MALFORMED_PDF) from None
        if visible < config.min_text_chars:
            raise PayslipError(PayslipErrorCode.SCANNED_UNSUPPORTED)
        return DocumentText(pages=tuple(pages), byte_size=len(data))
