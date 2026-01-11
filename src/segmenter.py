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
            # Mark as header and add period
            processed.append(f'[HEADER] {stripped}.')
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

    def segment_file(self, file_path: Path) -> list[Sentence]:
        """
        Segment a file into sentences. Supports .txt, .md, .pdf, .pptx, .docx files.

        Args:
            file_path: Path to the file.

        Returns:
            List of Sentence objects.
        """
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
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

        return self.segment(text)
