"""
PDF to text/markdown extraction using PyMuPDF.
"""

import re
from pathlib import Path
from dataclasses import dataclass

try:
    import fitz  # PyMuPDF
except ImportError:
    raise ImportError(
        "PyMuPDF not installed. Install with: pip install pymupdf"
    )


@dataclass
class TextBlock:
    """A block of text with metadata."""
    text: str
    font_size: float
    is_bold: bool
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    page_num: int


class PDFExtractor:
    """Extracts text from PDFs with structure detection."""

    def __init__(self, heading_size_threshold: float = 1.2):
        """
        Initialize the extractor.

        Args:
            heading_size_threshold: Font size ratio above median to consider as heading.
        """
        self.heading_size_threshold = heading_size_threshold

    def extract_text(self, pdf_path: Path) -> str:
        """
        Extract text from a PDF file.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text as a string.
        """
        doc = fitz.open(pdf_path)
        blocks = self._extract_blocks(doc)
        doc.close()

        return self._blocks_to_text(blocks)

    def extract_markdown(self, pdf_path: Path) -> str:
        """
        Extract text from a PDF file with markdown formatting.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text with markdown headings.
        """
        doc = fitz.open(pdf_path)
        blocks = self._extract_blocks(doc)
        doc.close()

        return self._blocks_to_markdown(blocks)

    def _extract_blocks(self, doc: fitz.Document) -> list[TextBlock]:
        """Extract text blocks with font information from all pages."""
        blocks = []

        for page_num, page in enumerate(doc):
            # Get detailed text with font info
            text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:  # Skip non-text blocks (images)
                    continue

                block_text = []
                font_sizes = []
                is_bold = False

                for line in block.get("lines", []):
                    line_text = []
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if text:
                            line_text.append(text)
                            font_sizes.append(span.get("size", 12))
                            # Check for bold
                            flags = span.get("flags", 0)
                            if flags & 2 ** 4:  # Bold flag
                                is_bold = True
                            font_name = span.get("font", "").lower()
                            if "bold" in font_name:
                                is_bold = True

                    if line_text:
                        block_text.append(" ".join(line_text))

                if block_text:
                    full_text = "\n".join(block_text)
                    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12

                    blocks.append(TextBlock(
                        text=full_text,
                        font_size=avg_font_size,
                        is_bold=is_bold,
                        bbox=tuple(block.get("bbox", (0, 0, 0, 0))),
                        page_num=page_num
                    ))

        return blocks

    def _detect_headings(self, blocks: list[TextBlock]) -> set[int]:
        """Detect which blocks are likely headings based on font size."""
        if not blocks:
            return set()

        # Calculate median font size
        font_sizes = sorted(b.font_size for b in blocks)
        median_size = font_sizes[len(font_sizes) // 2]

        heading_indices = set()
        for i, block in enumerate(blocks):
            # Consider as heading if:
            # - Larger than median * threshold
            # - Or bold and short (< 100 chars)
            # - Or short line without punctuation at end
            is_large = block.font_size > median_size * self.heading_size_threshold
            is_short_bold = block.is_bold and len(block.text) < 100
            is_short_no_punct = (
                len(block.text) < 80 and
                block.text and
                block.text[-1] not in '.!?;:'
            )

            if is_large or is_short_bold or (is_short_no_punct and block.is_bold):
                heading_indices.add(i)

        return heading_indices

    def _blocks_to_text(self, blocks: list[TextBlock]) -> str:
        """Convert blocks to plain text with paragraph breaks."""
        if not blocks:
            return ""

        heading_indices = self._detect_headings(blocks)
        lines = []

        for i, block in enumerate(blocks):
            text = block.text.strip()
            if not text:
                continue

            # Add extra newline before headings
            if i in heading_indices and lines:
                lines.append("")

            lines.append(text)

            # Add extra newline after headings
            if i in heading_indices:
                lines.append("")

        # Clean up multiple blank lines
        result = "\n".join(lines)
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()

    def _blocks_to_markdown(self, blocks: list[TextBlock]) -> str:
        """Convert blocks to markdown with heading markers."""
        if not blocks:
            return ""

        heading_indices = self._detect_headings(blocks)

        # Determine heading levels by font size
        if heading_indices:
            heading_blocks = [blocks[i] for i in heading_indices]
            heading_sizes = sorted(set(b.font_size for b in heading_blocks), reverse=True)
            size_to_level = {size: min(i + 1, 6) for i, size in enumerate(heading_sizes)}
        else:
            size_to_level = {}

        lines = []

        for i, block in enumerate(blocks):
            text = block.text.strip()
            if not text:
                continue

            if i in heading_indices:
                level = size_to_level.get(block.font_size, 2)
                prefix = "#" * level
                lines.append(f"\n{prefix} {text}\n")
            else:
                lines.append(text)
                lines.append("")  # Paragraph break

        # Clean up multiple blank lines
        result = "\n".join(lines)
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()


def extract_pdf_to_text(pdf_path: Path) -> str:
    """
    Convenience function to extract text from a PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted text.
    """
    extractor = PDFExtractor()
    return extractor.extract_text(pdf_path)


def extract_pdf_to_markdown(pdf_path: Path) -> str:
    """
    Convenience function to extract markdown from a PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted text with markdown headings.
    """
    extractor = PDFExtractor()
    return extractor.extract_markdown(pdf_path)
