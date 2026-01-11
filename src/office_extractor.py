"""
Extract text from Office documents (PPTX, DOCX).
"""

from pathlib import Path


def extract_pptx_to_text(pptx_path: Path) -> str:
    """
    Extract text from a PowerPoint file.

    Args:
        pptx_path: Path to the .pptx file.

    Returns:
        Extracted text with slide separations.
    """
    try:
        from pptx import Presentation
    except ImportError:
        raise ImportError(
            "python-pptx not installed. Install with: pip install python-pptx"
        )

    prs = Presentation(pptx_path)
    slides_text = []

    for slide_num, slide in enumerate(prs.slides, 1):
        slide_content = []

        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                text = shape.text.strip()
                # Check if this looks like a title (first text, short)
                if not slide_content and len(text) < 100:
                    slide_content.append(text)  # Title as heading
                else:
                    slide_content.append(text)

        if slide_content:
            slides_text.append("\n".join(slide_content))

    return "\n\n".join(slides_text)


def extract_docx_to_text(docx_path: Path) -> str:
    """
    Extract text from a Word document.

    Args:
        docx_path: Path to the .docx file.

    Returns:
        Extracted text with paragraph separations.
    """
    try:
        from docx import Document
    except ImportError:
        raise ImportError(
            "python-docx not installed. Install with: pip install python-docx"
        )

    doc = Document(docx_path)
    paragraphs = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            # Check if paragraph has heading style
            style_name = para.style.name.lower() if para.style else ""
            if "heading" in style_name or "title" in style_name:
                paragraphs.append(text)  # Will be detected as heading by segmenter
            else:
                paragraphs.append(text)

    return "\n\n".join(paragraphs)
