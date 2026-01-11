"""
CLI tool to convert PDF files to text or markdown.
"""

import argparse
from pathlib import Path

from .pdf_extractor import extract_pdf_to_text, extract_pdf_to_markdown


def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF files to text or markdown"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input PDF file or directory of PDFs"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output file or directory (default: same name with .txt/.md extension)"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["text", "markdown"],
        default="text",
        help="Output format (default: text)"
    )

    args = parser.parse_args()

    if args.input.is_dir():
        # Process directory
        pdf_files = list(args.input.glob("*.pdf"))
        if not pdf_files:
            print(f"No PDF files found in {args.input}")
            return

        output_dir = args.output or args.input
        output_dir.mkdir(parents=True, exist_ok=True)

        ext = ".md" if args.format == "markdown" else ".txt"

        for pdf_path in pdf_files:
            output_path = output_dir / f"{pdf_path.stem}{ext}"
            print(f"Converting {pdf_path.name} -> {output_path.name}")

            if args.format == "markdown":
                text = extract_pdf_to_markdown(pdf_path)
            else:
                text = extract_pdf_to_text(pdf_path)

            output_path.write_text(text, encoding="utf-8")

        print(f"Converted {len(pdf_files)} files to {output_dir}")

    else:
        # Process single file
        if not args.input.exists():
            print(f"File not found: {args.input}")
            return

        if args.output:
            output_path = args.output
        else:
            ext = ".md" if args.format == "markdown" else ".txt"
            output_path = args.input.with_suffix(ext)

        print(f"Converting {args.input.name} -> {output_path.name}")

        if args.format == "markdown":
            text = extract_pdf_to_markdown(args.input)
        else:
            text = extract_pdf_to_text(args.input)

        output_path.write_text(text, encoding="utf-8")
        print(f"Wrote {len(text)} characters to {output_path}")


if __name__ == "__main__":
    main()
