"""Helpers for validating user supplied document uploads."""


def validate_pdf_upload(upload):
    """Check the PDF signature rather than trusting the browser MIME header."""
    try:
        upload.seek(0)
        header = upload.read(1024)
    finally:
        upload.seek(0)
    if b"%PDF-" not in header:
        raise ValueError("Upload a valid PDF file.")
    return upload
