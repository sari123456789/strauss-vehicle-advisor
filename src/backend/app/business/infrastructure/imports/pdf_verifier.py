"""Challenge-instructions PDF: existence, integrity (manifest SHA-256) and readability only.

No text is extracted into rules and nothing in the PDF derives or overrides a business rule.
"""

import io

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.business.application.ports.reference_data import PdfSource
from app.business.infrastructure.imports.source_document import SourceDocument, SourceIntegrityError


def verify_pdf(document: SourceDocument) -> PdfSource:
    path = document.fingerprint.path
    if not document.raw.startswith(b"%PDF-"):
        raise SourceIntegrityError(f"{path}: not a PDF file")
    try:
        reader = PdfReader(io.BytesIO(document.raw), strict=False)
        if reader.is_encrypted:
            raise SourceIntegrityError(f"{path}: the PDF is encrypted")
        page_count = len(reader.pages)
    except PyPdfError as error:
        raise SourceIntegrityError(f"{path}: the PDF cannot be parsed ({error})") from error
    if page_count < 1:
        raise SourceIntegrityError(f"{path}: the PDF has no pages")
    return PdfSource(pdf=document.fingerprint, page_count=page_count)
