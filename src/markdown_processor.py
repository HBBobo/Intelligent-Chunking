"""
Markdown-aware sentence extraction using mistune AST parser.

Extracts sentences from markdown with structural tokens like [H1], [H2],
[LIST_ITEM], etc. to preserve document structure for boundary scoring.
"""

from dataclasses import dataclass

try:
    import mistune
except ImportError:
    raise ImportError(
        "mistune not installed. Install with: pip install mistune"
    )

import spacy


@dataclass
class StructuredSentence:
    """Sentence with structural context."""
    text: str
    index: int
    structure_type: str = "paragraph"
    depth: int = 0


class MarkdownProcessor:
    """Extract sentences from markdown with structural tokens."""

    def __init__(self, spacy_model: str = "en_core_web_sm"):
        """
        Initialize the processor.

        Args:
            spacy_model: spaCy model name for paragraph sentence splitting.
        """
        self.md = mistune.create_markdown(renderer=None)  # AST mode
        try:
            self.nlp = spacy.load(spacy_model, disable=["ner", "lemmatizer"])
            self.nlp.max_length = 2_000_000
        except OSError:
            raise RuntimeError(
                f"spaCy model '{spacy_model}' not found. "
                f"Install it with: python -m spacy download {spacy_model}"
            )

    def process(self, markdown: str) -> list[StructuredSentence]:
        """
        Extract structured sentences from markdown.

        Args:
            markdown: Markdown text to process.

        Returns:
            List of StructuredSentence objects with text and metadata.
        """
        tokens = self.md(markdown)
        sentences = []
        self._walk_tokens(tokens, sentences, depth=0)

        # Re-index sentences
        for i, s in enumerate(sentences):
            s.index = i
        return sentences

    def _walk_tokens(self, tokens: list, sentences: list, depth: int):
        """Recursively walk AST and extract sentences."""
        for token in tokens:
            token_type = token.get('type', '')

            if token_type == 'heading':
                level = token.get('attrs', {}).get('level', 1)
                text = self._extract_text(token.get('children', []))
                if text.strip():
                    # Add period if heading doesn't end with punctuation
                    clean_text = text.strip()
                    if clean_text and clean_text[-1] not in '.!?':
                        clean_text += '.'
                    sentences.append(StructuredSentence(
                        text=f"[H{level}] {clean_text}",
                        index=-1,
                        structure_type=f"H{level}",
                        depth=depth
                    ))

            elif token_type == 'paragraph':
                text = self._extract_text(token.get('children', []))
                if text.strip():
                    # Use spaCy for long paragraphs with multiple sentences
                    if len(text) > 150 and any(p in text for p in '.!?'):
                        doc = self.nlp(text)
                        for sent in doc.sents:
                            sent_text = sent.text.strip()
                            if sent_text:
                                sentences.append(StructuredSentence(
                                    text=sent_text,
                                    index=-1,
                                    structure_type="paragraph",
                                    depth=depth
                                ))
                    else:
                        sentences.append(StructuredSentence(
                            text=text.strip(),
                            index=-1,
                            structure_type="paragraph",
                            depth=depth
                        ))

            elif token_type == 'list':
                ordered = token.get('attrs', {}).get('ordered', False)
                item_type = "NUMBERED_ITEM" if ordered else "LIST_ITEM"
                for child in token.get('children', []):
                    if child.get('type') == 'list_item':
                        self._process_list_item(child, sentences, item_type, depth + 1)

            elif token_type == 'block_code':
                # Skip code blocks or add placeholder
                sentences.append(StructuredSentence(
                    text="[CODE_BLOCK]",
                    index=-1,
                    structure_type="CODE_BLOCK",
                    depth=depth
                ))

            elif token_type == 'image':
                # Skip images entirely
                pass

            elif token_type == 'thematic_break':
                # Skip horizontal rules
                pass

            elif token_type == 'block_quote':
                # Process blockquote content
                children = token.get('children', [])
                if children:
                    self._walk_tokens(children, sentences, depth + 1)

            elif token_type == 'table':
                # Extract table content as sentences
                self._process_table(token, sentences, depth)

            elif 'children' in token:
                # Recurse for other container types
                self._walk_tokens(token['children'], sentences, depth)

    def _process_list_item(self, token: dict, sentences: list, item_type: str, depth: int):
        """Process list item with potential nested content."""
        text_parts = []
        nested_lists = []

        for child in token.get('children', []):
            if child.get('type') == 'paragraph':
                text = self._extract_text(child.get('children', []))
                if text.strip():
                    text_parts.append(text.strip())
            elif child.get('type') == 'list':
                # Collect nested lists to process after main text
                nested_lists.append(child)
            elif child.get('type') == 'block_text':
                # Some list items have block_text instead of paragraph
                text = self._extract_text(child.get('children', []))
                if text.strip():
                    text_parts.append(text.strip())

        # Add main list item text
        if text_parts:
            combined_text = ' '.join(text_parts)
            sentences.append(StructuredSentence(
                text=f"[{item_type}] {combined_text}",
                index=-1,
                structure_type=item_type,
                depth=depth
            ))

        # Process nested lists
        for nested_list in nested_lists:
            ordered = nested_list.get('attrs', {}).get('ordered', False)
            nested_type = "NUMBERED_ITEM" if ordered else "LIST_ITEM"
            for item in nested_list.get('children', []):
                if item.get('type') == 'list_item':
                    self._process_list_item(item, sentences, nested_type, depth + 1)

    def _process_table(self, token: dict, sentences: list, depth: int):
        """Extract text from table cells as sentences."""
        # Tables have head and body
        head = token.get('children', [{}])[0] if token.get('children') else {}
        body = token.get('children', [{}])[1] if len(token.get('children', [])) > 1 else {}

        # Process header row
        if head.get('type') == 'table_head':
            for row in head.get('children', []):
                if row.get('type') == 'table_row':
                    cells = []
                    for cell in row.get('children', []):
                        if cell.get('type') == 'table_cell':
                            text = self._extract_text(cell.get('children', []))
                            if text.strip():
                                cells.append(text.strip())
                    if cells:
                        sentences.append(StructuredSentence(
                            text=f"[TABLE_HEADER] {' | '.join(cells)}",
                            index=-1,
                            structure_type="TABLE_HEADER",
                            depth=depth
                        ))

        # Process body rows
        if body.get('type') == 'table_body':
            for row in body.get('children', []):
                if row.get('type') == 'table_row':
                    cells = []
                    for cell in row.get('children', []):
                        if cell.get('type') == 'table_cell':
                            text = self._extract_text(cell.get('children', []))
                            if text.strip():
                                cells.append(text.strip())
                    if cells:
                        sentences.append(StructuredSentence(
                            text=f"[TABLE_ROW] {' | '.join(cells)}",
                            index=-1,
                            structure_type="TABLE_ROW",
                            depth=depth
                        ))

    def _extract_text(self, children: list) -> str:
        """Extract plain text from inline children, stripping formatting."""
        parts = []
        for child in children:
            child_type = child.get('type', '')

            if child_type == 'text':
                parts.append(child.get('raw', ''))

            elif child_type in ['strong', 'emphasis']:
                # Strip bold/italic but keep text
                if 'children' in child:
                    parts.append(self._extract_text(child['children']))

            elif child_type == 'codespan':
                # Keep inline code content
                parts.append(child.get('raw', ''))

            elif child_type == 'link':
                # Extract link text, ignore URL
                if 'children' in child:
                    parts.append(self._extract_text(child['children']))

            elif child_type == 'image':
                # Skip inline images
                pass

            elif child_type == 'softbreak':
                parts.append(' ')

            elif child_type == 'linebreak':
                parts.append(' ')

            elif 'children' in child:
                parts.append(self._extract_text(child['children']))

            elif 'raw' in child:
                parts.append(child['raw'])

        return ''.join(parts)


# Special tokens that should be registered with the tokenizer
SPECIAL_TOKENS = [
    '[H1]', '[H2]', '[H3]', '[H4]', '[H5]', '[H6]',
    '[LIST_ITEM]', '[NUMBERED_ITEM]',
    '[CODE_BLOCK]',
    '[TABLE_HEADER]', '[TABLE_ROW]'
]
