"""
PDF to Markdown extraction using Mistral OCR API.

Uses Mistral's state-of-the-art OCR model (mistral-ocr-latest) for
high-quality markdown extraction from PDFs.

For large PDFs that exceed API limits, automatically splits into
smaller page chunks and processes them in parallel.
"""

import asyncio
import base64
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv()

# Maximum pages per chunk to avoid API size limits
MAX_PAGES_PER_CHUNK = 20

# Maximum concurrent OCR API calls
MAX_OCR_CONCURRENCY = 10


def safe_print(msg: str) -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg, file=sys.stderr)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(), file=sys.stderr)


class MistralOCRExtractor:
    """Extracts text from PDFs using Mistral OCR API with async support."""

    def __init__(
        self,
        api_key: str = None,
        max_pages_per_chunk: int = MAX_PAGES_PER_CHUNK,
        max_concurrency: int = MAX_OCR_CONCURRENCY
    ):
        """
        Initialize the extractor.

        Args:
            api_key: Mistral API key. If None, reads from MISTRAL_API_KEY env var.
            max_pages_per_chunk: Maximum pages to process in a single API call.
            max_concurrency: Maximum concurrent OCR API calls.
        """
        try:
            from mistralai import Mistral
        except ImportError:
            raise ImportError(
                "mistralai not installed. Install with: pip install mistralai"
            )

        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Mistral API key required. Set MISTRAL_API_KEY in .env or pass api_key."
            )
        self.client = Mistral(api_key=self.api_key)
        self.max_pages_per_chunk = max_pages_per_chunk
        self.max_concurrency = max_concurrency
        self._semaphore = None  # Created per async call

    def _get_page_count(self, pdf_path: Path) -> int:
        """Get the number of pages in a PDF."""
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count

    def _extract_page_range(self, pdf_path: Path, start_page: int, end_page: int) -> bytes:
        """
        Extract a range of pages from a PDF as bytes.

        Args:
            pdf_path: Path to the PDF file.
            start_page: First page (0-indexed).
            end_page: Last page (exclusive).

        Returns:
            PDF bytes containing only the specified pages.
        """
        import fitz  # PyMuPDF

        src_doc = fitz.open(pdf_path)
        new_doc = fitz.open()

        # Insert pages from source
        new_doc.insert_pdf(src_doc, from_page=start_page, to_page=end_page - 1)

        # Write to bytes
        pdf_bytes = new_doc.tobytes()

        new_doc.close()
        src_doc.close()

        return pdf_bytes

    def _process_pdf_bytes_sync(self, pdf_bytes: bytes) -> str:
        """
        Process PDF bytes through Mistral OCR API (synchronous).

        Args:
            pdf_bytes: Raw PDF bytes.

        Returns:
            Extracted markdown text.
        """
        base64_pdf = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        response = self.client.ocr.process(
            model="mistral-ocr-latest",
            document={
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{base64_pdf}"
            },
            include_image_base64=False
        )

        markdown_parts = []
        for page in response.pages:
            if page.markdown:
                markdown_parts.append(page.markdown)

        return "\n\n".join(markdown_parts)

    async def _process_chunk_async(
        self,
        pdf_path: Path,
        start_page: int,
        end_page: int,
        chunk_num: int,
        executor: ThreadPoolExecutor
    ) -> tuple[int, str]:
        """
        Process a single chunk asynchronously.

        Args:
            pdf_path: Path to the PDF file.
            start_page: First page (0-indexed).
            end_page: Last page (exclusive).
            chunk_num: Chunk number for ordering.
            executor: Thread pool for running sync code.

        Returns:
            Tuple of (chunk_num, markdown_text) for ordering.
        """
        async with self._semaphore:
            safe_print(f"  Processing chunk {chunk_num}: pages {start_page + 1}-{end_page}...")

            # Extract pages (sync, fast)
            chunk_bytes = self._extract_page_range(pdf_path, start_page, end_page)

            # Run OCR API call in thread pool
            loop = asyncio.get_event_loop()
            markdown = await loop.run_in_executor(
                executor,
                self._process_pdf_bytes_sync,
                chunk_bytes
            )

            return (chunk_num, markdown)

    async def extract_markdown_async(self, pdf_path: Path) -> str:
        """
        Extract markdown from a PDF file using Mistral OCR (async).

        For large PDFs, processes chunks in parallel.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text as markdown.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        # Get page count
        page_count = self._get_page_count(pdf_path)

        # If small enough, process directly
        if page_count <= self.max_pages_per_chunk:
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            return self._process_pdf_bytes_sync(pdf_bytes)

        # Split into chunks and process in parallel
        safe_print(f"Large PDF ({page_count} pages) - processing {self.max_concurrency} chunks in parallel...")

        self._semaphore = asyncio.Semaphore(self.max_concurrency)

        # Create chunk tasks
        tasks = []
        chunk_num = 0

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            for start_page in range(0, page_count, self.max_pages_per_chunk):
                end_page = min(start_page + self.max_pages_per_chunk, page_count)
                chunk_num += 1

                task = self._process_chunk_async(
                    pdf_path, start_page, end_page, chunk_num, executor
                )
                tasks.append(task)

            # Wait for all chunks
            results = await asyncio.gather(*tasks)

        # Sort by chunk number and combine
        results.sort(key=lambda x: x[0])
        all_markdown = [markdown for _, markdown in results]

        safe_print(f"  Completed {chunk_num} chunks.")

        return "\n\n".join(all_markdown)

    def extract_markdown(self, pdf_path: Path) -> str:
        """
        Extract markdown from a PDF file using Mistral OCR (sync wrapper).

        For large PDFs, automatically splits into chunks and processes in parallel.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text as markdown.
        """
        # Run async version in event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in async context - use run_in_executor workaround
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.extract_markdown_async(pdf_path))
                    return future.result()
            else:
                return loop.run_until_complete(self.extract_markdown_async(pdf_path))
        except RuntimeError:
            # No event loop exists
            return asyncio.run(self.extract_markdown_async(pdf_path))


def extract_pdf_to_markdown_mistral(pdf_path: Path) -> str:
    """
    Convenience function to extract markdown from PDF using Mistral OCR.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted markdown text.
    """
    extractor = MistralOCRExtractor()
    return extractor.extract_markdown(pdf_path)
