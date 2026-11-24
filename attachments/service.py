"""Bounded local PDF/text extraction, with OCR only for image-only pages."""
import hashlib
import warnings
from PIL import Image
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import time

from attachments import constants
from attachments.records import AttachmentText
from core.tracing import tracing


class AttachmentExtractor:
    def extract(self, data: bytes, name: str, mime: str, source: str) -> AttachmentText:
        name = Path(name).name[:constants.MAX_NAME_CHARS] or 'attachment'
        digest = hashlib.sha256(data).hexdigest()
        with tracing.operation('attachment.extract', {'name': name, 'source': source, 'bytes': len(data)}, private=True) as span:
            result = self._extract(data, name, mime, source, digest)
            tracing.record_output(span, {'source': source, 'status': result.status, 'text': result.text, 'notice': result.notice})
            return result

    def _extract(self, data: bytes, name: str, mime: str, source: str, digest: str) -> AttachmentText:
        def result(text: str = '', status: str = 'unreadable', notice: str = '') -> AttachmentText:
            return AttachmentText(name, source, text[:constants.MAX_TEXT_CHARS], status, notice, digest, constants.EXTRACTION_VERSION)
        if len(data) > constants.MAX_FILE_BYTES:
            return result(status='too_large', notice='File exceeds the 5 MB reading limit.')
        if mime not in constants.SUPPORTED_TYPES:
            return result(status='unsupported', notice='Supported formats: PDF, PNG, JPEG and UTF-8 text.')
        if not data:
            return result(notice='File is empty.')
        try:
            deadline = time.monotonic() + constants.MAX_EXTRACTION_SECONDS
            with TemporaryDirectory(prefix='personal-agent-attachment-') as directory:
                root = Path(directory)
                path = root / 'input'
                path.write_bytes(data)
                if mime == 'text/plain':
                    text = data.decode('utf-8')
                    partial = len(text) > constants.MAX_TEXT_CHARS
                elif mime == 'application/pdf':
                    if not data.startswith(b'%PDF-'):
                        return result(notice='File content is not a PDF.')
                    info = self.command(['pdfinfo', str(path)], root / 'info', deadline)
                    pages_match = re.search(r'^Pages:\s+(\d+)', info, re.MULTILINE)
                    if not pages_match:
                        return result(notice='Could not determine PDF page count.')
                    pages = int(pages_match[1])
                    partial = pages > constants.MAX_PAGES
                    chunks = []
                    for page in range(1, min(pages, constants.MAX_PAGES) + 1):
                        text = self.command(['pdftotext', '-f', str(page), '-l', str(page), '-layout', str(path), '-'], root / 'text', deadline).strip()
                        if not text:
                            image = root / 'page'
                            self.command(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile', '-scale-to', str(constants.OCR_IMAGE_SIZE), '-png', str(path), str(image)], root / 'render', deadline)
                            text = self.command(['tesseract', str(image) + '.png', 'stdout', '-l', 'eng'], root / 'ocr', deadline).strip()
                        if not text:
                            partial = True
                            text = '[No readable text on this page.]'
                        chunks.append(f'[page {page}]\n{text}')
                        if sum(map(len, chunks)) > constants.MAX_TEXT_CHARS:
                            partial = True
                            break
                    text = '\n\n'.join(chunks)
                else:
                    valid = data.startswith(b'\x89PNG\r\n\x1a\n') if mime == 'image/png' else data.startswith(b'\xff\xd8\xff')
                    if not valid:
                        return result(notice='Image bytes do not match the declared image format.')
                    with warnings.catch_warnings():
                        warnings.simplefilter('error', Image.DecompressionBombWarning)
                        with Image.open(path) as frame:
                            if frame.width * frame.height > constants.MAX_IMAGE_PIXELS:
                                return result(notice='Image resolution exceeds the reading limit.')
                            frame.thumbnail((constants.OCR_IMAGE_SIZE, constants.OCR_IMAGE_SIZE))
                            frame.convert('RGB').save(root / 'image.png')
                    path = root / 'image.png'
                    text = '[image OCR]\n' + self.command(['tesseract', str(path), 'stdout', '-l', 'eng'], root / 'ocr', deadline).strip()
                    partial = len(text) > constants.MAX_TEXT_CHARS
                if not text.strip() or text.strip() == '[image OCR]':
                    return result(notice='No readable text found. Try a clearer image or a text PDF.')
                return result(text, 'partial' if partial else 'read', 'Only a bounded excerpt was read.' if partial else '')
        except FileNotFoundError:
            return result(notice='Local extraction tools are missing. Install Poppler and Tesseract.')
        except (subprocess.SubprocessError, OSError, UnicodeError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            return result(notice='Could not read this file: it may be encrypted, damaged, or exceed the processing time limit.')

    @staticmethod
    def command(args: list[str], output: Path, deadline: float) -> str:
        timeout = min(constants.COMMAND_TIMEOUT_SECONDS, deadline - time.monotonic())
        if timeout <= 0:
            raise subprocess.TimeoutExpired(args[0], 0)
        with output.open('wb') as stream:
            subprocess.run(args, stdout=stream, stderr=subprocess.DEVNULL, check=True, timeout=timeout)
        with output.open('rb') as stream:
            return stream.read(constants.MAX_TEXT_CHARS * 4 + constants.OUTPUT_BUFFER_EXTRA_BYTES).decode('utf-8', errors='replace')
