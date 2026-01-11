"""
Sentence segmentation using spaCy.
"""

import re
from dataclasses import dataclass
from pathlib import Path
import spacy


def preprocess_text(text: str) -> str:
    """
    Normalize text before sentence segmentation.
    - Split headings from body text by adding periods
    - Normalize multiple newlines to single paragraph breaks
    """
    lines = text.split('\n')
    processed = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            processed.append('')
            continue

        # Check if this looks like a heading:
        # - Short (< 80 chars)
        # - Doesn't end with sentence punctuation
        # - Followed by blank line or EOF
        is_heading = (
            len(stripped) < 80 and
            stripped[-1] not in '.!?:;' and
            (i + 1 >= len(lines) or not lines[i + 1].strip())
        )

        if is_heading:
            # Add period to make it a sentence (no [HEADER] marker - can cause confusion)
            processed.append(f'{stripped}.')
        else:
            processed.append(line)

    # Normalize multiple blank lines to single
    result = '\n'.join(processed)
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result


@dataclass
class Sentence:
    """A sentence with its text and position in the document."""
    text: str
    start_char: int
    end_char: int
    index: int


class SentenceSegmenter:
    """Segments documents into sentences using spaCy."""

    def __init__(self, model_name: str = "en_core_web_sm"):
        """
        Initialize the segmenter with a spaCy model.

        Args:
            model_name: Name of the spaCy model to use.
        """
        try:
            self.nlp = spacy.load(model_name, disable=["ner", "lemmatizer"])
        except OSError:
            raise RuntimeError(
                f"spaCy model '{model_name}' not found. "
                f"Install it with: python -m spacy download {model_name}"
            )

        # Increase max length for long documents
        self.nlp.max_length = 2_000_000

    def segment(self, text: str) -> list[Sentence]:
        """
        Segment text into sentences.

        Args:
            text: The document text to segment.

        Returns:
            List of Sentence objects with text and positions.
        """
        # Check if input looks like markdown
        if self._is_markdown(text):
            return self._segment_markdown(text)
        else:
            return self._segment_plain_text(text)

    def _is_markdown(self, text: str) -> bool:
        """
        Detect if text contains markdown syntax.

        Checks for common markdown patterns that indicate structured content.
        """
        # Look for markdown-specific patterns
        md_patterns = [
            '\n# ',      # H1 header
            '\n## ',     # H2 header
            '\n### ',    # H3 header
            '\n![',      # Image
            '\n- ',      # Bullet list
            '\n* ',      # Alternate bullet
            '\n1. ',     # Numbered list
            '**',        # Bold
            '```',       # Code block
        ]
        # Also check start of text
        start_patterns = ['# ', '## ', '### ', '![', '- ', '* ', '1. ']

        has_start_pattern = any(text.startswith(p) for p in start_patterns)
        has_inline_pattern = any(p in text for p in md_patterns)

        return has_start_pattern or has_inline_pattern

    def _segment_markdown(self, text: str) -> list[Sentence]:
        """
        Segment markdown using AST parser with structural tokens.

        Uses mistune to parse markdown and extract sentences with
        special tokens like [H1], [H2], [LIST_ITEM], etc.
        """
        from .markdown_processor import MarkdownProcessor

        # Get full model name (e.g., 'en_core_web_sm')
        lang = self.nlp.meta.get('lang', 'en')
        name = self.nlp.meta.get('name', 'core_web_sm')
        full_name = f"{lang}_{name}" if not name.startswith(lang) else name
        processor = MarkdownProcessor(spacy_model=full_name)
        structured = processor.process(text)

        return [
            Sentence(
                text=s.text,
                start_char=0,  # Position not tracked for markdown
                end_char=len(s.text),
                index=s.index
            )
            for s in structured
        ]

    def _segment_plain_text(self, text: str) -> list[Sentence]:
        """
        Original spaCy-based segmentation for plain text.

        Preprocesses text to handle headings and normalize whitespace,
        then uses spaCy for sentence boundary detection.
        """
        # Preprocess to handle headings and normalize whitespace
        text = preprocess_text(text)
        doc = self.nlp(text)
        sentences = []

        for idx, sent in enumerate(doc.sents):
            # Clean up whitespace but preserve original positions
            sent_text = sent.text.strip()
            if sent_text:  # Skip empty sentences
                sentences.append(Sentence(
                    text=sent_text,
                    start_char=sent.start_char,
                    end_char=sent.end_char,
                    index=len(sentences)
                ))

        return sentences

    def segment_file(self, file_path: Path, save_markdown: bool = True) -> list[Sentence]:
        """
        Segment a file into sentences. Supports .txt, .md, .pdf, .pptx, .docx files.

        Args:
            file_path: Path to the file.
            save_markdown: If True, save extracted markdown to data/markdown/.

        Returns:
            List of Sentence objects.
        """
        suffix = file_path.suffix.lower()
        text = None
        is_markdown = False

        if suffix == ".pdf":
            # Check for existing markdown first (skip OCR if already extracted)
            existing_md = self._find_existing_markdown(file_path)
            if existing_md:
                import sys
                print(f"Using cached markdown for {file_path.name}", file=sys.stderr)
                text = existing_md
                is_markdown = True
            else:
                # Try Mistral OCR first (better quality), fallback to PyMuPDF
                try:
                    from .mistral_ocr import extract_pdf_to_markdown_mistral
                    text = extract_pdf_to_markdown_mistral(file_path)
                    is_markdown = True
                except Exception as e:
                    # Fallback to PyMuPDF if Mistral fails (API error, size limit, no key, etc.)
                    import sys
                    print(f"Mistral OCR failed for {file_path.name}: {e}", file=sys.stderr)
                    print(f"Falling back to PyMuPDF...", file=sys.stderr)
                    from .pdf_extractor import extract_pdf_to_text
                    text = extract_pdf_to_text(file_path)
        elif suffix == ".pptx":
            from .office_extractor import extract_pptx_to_text
            text = extract_pptx_to_text(file_path)
        elif suffix == ".docx":
            from .office_extractor import extract_docx_to_text
            text = extract_docx_to_text(file_path)
        else:
            text = file_path.read_text(encoding="utf-8")
            is_markdown = suffix == ".md"

        # Save markdown if extracted from PDF or is markdown file
        if save_markdown and is_markdown and text:
            self._save_markdown(file_path, text)

        return self.segment(text)

    def _find_existing_markdown(self, source_path: Path) -> str | None:
        """Check if markdown already exists for this file."""
        # Find project root (where data/ is)
        project_root = source_path.parent
        while project_root.name != "data" and project_root.parent != project_root:
            project_root = project_root.parent
        if project_root.name == "data":
            project_root = project_root.parent

        markdown_path = project_root / "data" / "markdown" / f"{source_path.stem}.md"
        if markdown_path.exists():
            return markdown_path.read_text(encoding="utf-8")
        return None

    def _save_markdown(self, source_path: Path, text: str) -> None:
        """Save extracted markdown to data/markdown/ directory."""
        # Find project root (where data/ is)
        project_root = source_path.parent
        while project_root.name != "data" and project_root.parent != project_root:
            project_root = project_root.parent
        if project_root.name == "data":
            project_root = project_root.parent

        markdown_dir = project_root / "data" / "markdown"
        markdown_dir.mkdir(parents=True, exist_ok=True)

        output_path = markdown_dir / f"{source_path.stem}.md"
        output_path.write_text(text, encoding="utf-8")
