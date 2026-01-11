"""
Label documents that already have cached markdown (skip OCR).
Runs in parallel with the main pipeline that's doing OCR.
"""

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))


def safe_print(msg: str) -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode())


from src.segmenter import SentenceSegmenter
from src.window import WindowManager
from src.gemini_client import GeminiClient
from src.labeler import BoundaryLabeler
from src.reconciler import ScoreReconciler


async def main():
    markdown_dir = Path("data/markdown")
    raw_dir = Path("data/raw")
    output_path = Path("data/processed/cached_training_data.jsonl")
    sentences_dir = Path("data/processed/sentences")
    sentences_dir.mkdir(parents=True, exist_ok=True)

    # Find PDFs that have cached markdown
    cached_pdfs = []
    for md_file in markdown_dir.glob("*.md"):
        pdf_name = md_file.stem + ".pdf"
        pdf_path = raw_dir / pdf_name
        if pdf_path.exists():
            cached_pdfs.append(pdf_path)

    safe_print(f"Found {len(cached_pdfs)} PDFs with cached markdown")

    if not cached_pdfs:
        safe_print("No cached PDFs to process")
        return

    # Initialize components
    segmenter = SentenceSegmenter()
    window_manager = WindowManager(window_size=50, overlap=10)
    client = GeminiClient(concurrency=5)
    labeler = BoundaryLabeler(client)
    reconciler = ScoreReconciler()

    all_results = []
    total_sentences = 0

    for i, pdf_path in enumerate(cached_pdfs):
        safe_print(f"\n[{i+1}/{len(cached_pdfs)}] Processing {pdf_path.name}...")

        # Segment (will use cached markdown)
        sentences = segmenter.segment_file(pdf_path, save_markdown=False)
        total_sentences += len(sentences)
        safe_print(f"  {len(sentences)} sentences")

        if len(sentences) < 2:
            safe_print("  Skipping (not enough sentences)")
            continue

        doc_id = pdf_path.stem

        # Save sentences
        sentences_file = sentences_dir / f"{doc_id}.json"
        with open(sentences_file, "w", encoding="utf-8") as f:
            json.dump({
                "doc_id": doc_id,
                "sentences": [s.text for s in sentences]
            }, f, ensure_ascii=False, indent=2)

        # Create windows
        windows = window_manager.create_windows(sentences, doc_id=doc_id)
        safe_print(f"  {len(windows)} windows")

        # Label windows concurrently
        tasks = [labeler.label_window(window) for window in windows]
        window_results = []

        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result:
                window_results.extend(result)

        safe_print(f"  {len(window_results)} boundary scores")

        # Reconcile
        reconciled = reconciler.reconcile(window_results)
        all_results.append((doc_id, reconciled))
        safe_print(f"  {len(reconciled)} unique boundaries after reconciliation")

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        for doc_id, boundaries in all_results:
            for boundary in boundaries:
                record = {
                    "doc_id": boundary.doc_id,
                    "boundary_idx": boundary.boundary_idx,
                    "sentence_idx_before": boundary.boundary_idx,
                    "sentence_idx_after": boundary.boundary_idx + 1,
                    "score": boundary.score
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    total_boundaries = sum(len(b) for _, b in all_results)
    safe_print(f"\n{'='*60}")
    safe_print(f"DONE: {len(cached_pdfs)} documents, {total_sentences} sentences, {total_boundaries} boundaries")
    safe_print(f"Output: {output_path}")
    safe_print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
