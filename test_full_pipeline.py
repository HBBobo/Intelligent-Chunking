"""
Test the full training data generation pipeline on document.pdf.
"""

import asyncio
import json
from pathlib import Path

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from src.segmenter import SentenceSegmenter
from src.window import WindowManager
from src.gemini_client import GeminiClient
from src.labeler import BoundaryLabeler
from src.reconciler import ScoreReconciler


async def main():
    pdf_path = Path("data/raw/document.pdf")
    output_path = Path("data/processed/document_pipeline_test.jsonl")
    sentences_dir = Path("data/processed/sentences")
    sentences_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("FULL PIPELINE TEST: document.pdf")
    print("=" * 60)

    # Step 1: Segment document
    print("\n[1/5] Segmenting document...")
    segmenter = SentenceSegmenter()
    sentences = segmenter.segment_file(pdf_path)
    print(f"  Extracted {len(sentences)} sentences")

    # Save sentences
    doc_id = pdf_path.stem
    sentences_file = sentences_dir / f"{doc_id}.json"
    with open(sentences_file, "w", encoding="utf-8") as f:
        json.dump({
            "doc_id": doc_id,
            "sentences": [s.text for s in sentences]
        }, f, ensure_ascii=False, indent=2)
    print(f"  Saved sentences to {sentences_file}")

    # Step 2: Create windows
    print("\n[2/5] Creating windows...")
    window_manager = WindowManager(window_size=50, overlap=10)
    windows = window_manager.create_windows(sentences, doc_id=doc_id)
    print(f"  Created {len(windows)} windows")

    # Step 3: Initialize Gemini client and labeler
    print("\n[3/5] Initializing Gemini client...")
    client = GeminiClient(concurrency=3)
    labeler = BoundaryLabeler(client)
    print("  Client initialized")

    # Step 4: Label windows
    print("\n[4/5] Labeling windows with Gemini...")
    all_results = []

    for i, window in enumerate(windows):
        print(f"  Processing window {i+1}/{len(windows)} "
              f"(sentences {window.start_idx}-{window.start_idx + len(window.sentences) - 1})...", end=" ")

        results = await labeler.label_window(window)

        if results:
            all_results.extend(results)
            print(f"OK ({len(results)} boundaries)")
        else:
            print("FAILED")

    print(f"  Total boundaries labeled: {len(all_results)}")

    # Step 5: Reconcile overlapping scores
    print("\n[5/5] Reconciling overlapping scores...")
    reconciler = ScoreReconciler()
    final_boundaries = reconciler.reconcile(all_results)
    print(f"  Final boundaries after reconciliation: {len(final_boundaries)}")

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        for boundary in final_boundaries:
            record = {
                "doc_id": boundary.doc_id,
                "boundary_idx": boundary.boundary_idx,
                "sentence_idx_before": boundary.boundary_idx,
                "sentence_idx_after": boundary.boundary_idx + 1,
                "score": boundary.score
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nOutput saved to: {output_path}")

    # Print score distribution
    scores = [b.score for b in final_boundaries]
    print("\nScore distribution:")
    for score in sorted(set(scores)):
        count = scores.count(score)
        pct = count / len(scores) * 100
        bar = "#" * int(pct / 2)
        print(f"  {score}: {count:3d} ({pct:5.1f}%) {bar}")

    print("\n" + "=" * 60)
    print("PIPELINE TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
