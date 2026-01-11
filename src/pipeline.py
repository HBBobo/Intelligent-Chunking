"""
Main orchestration pipeline for training data generation.
"""

import asyncio
import json
import sys
from pathlib import Path
from tqdm import tqdm


def safe_print(msg: str = "") -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode())


from .config import (
    DEFAULT_WINDOW_SIZE,
    DEFAULT_OVERLAP,
    DEFAULT_CONCURRENCY,
)
from .segmenter import Sentence
from .segmenter import SentenceSegmenter
from .window import WindowManager
from .gemini_client import GeminiClient
from .labeler import BoundaryLabeler
from .reconciler import ScoreReconciler, BoundaryScore


class Pipeline:
    """Orchestrates the full training data generation pipeline."""

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        overlap: int = DEFAULT_OVERLAP,
        concurrency: int = DEFAULT_CONCURRENCY
    ):
        """
        Initialize the pipeline.

        Args:
            window_size: Number of sentences per window.
            overlap: Number of overlapping sentences between windows.
            concurrency: Maximum concurrent API calls.
        """
        self.segmenter = SentenceSegmenter()
        self.window_manager = WindowManager(window_size, overlap)
        self.client = GeminiClient(concurrency)
        self.labeler = BoundaryLabeler(self.client)
        self.reconciler = ScoreReconciler(strategy="max")

    async def process_document(
        self,
        file_path: Path,
        progress_bar: tqdm | None = None
    ) -> tuple[list[Sentence], list[BoundaryScore]]:
        """
        Process a single document and return sentences and boundary scores.

        Args:
            file_path: Path to the file (.txt, .md, .pdf, .pptx, .docx).
            progress_bar: Optional progress bar for tracking windows.

        Returns:
            Tuple of (sentences list, reconciled BoundaryScore objects).
        """
        # Read and segment document (handles PDF, txt, md, pptx, docx)
        sentences = self.segmenter.segment_file(file_path)

        if len(sentences) < 2:
            safe_print(f"Skipping {file_path.name}: not enough sentences ({len(sentences)})")
            return sentences, []

        doc_id = file_path.stem

        # Create windows
        windows = self.window_manager.create_windows(sentences, doc_id)

        if not windows:
            return sentences, []

        # Label all windows concurrently
        tasks = [self.labeler.label_window(window) for window in windows]

        all_scores = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                all_scores.extend(result)
            if progress_bar:
                progress_bar.update(1)

        # Reconcile overlapping scores
        reconciled = self.reconciler.reconcile(all_scores)

        return sentences, reconciled

    async def process_directory(
        self,
        input_dir: Path,
        output_path: Path,
        file_extensions: tuple[str, ...] = (".txt", ".md", ".pdf", ".pptx", ".docx")
    ) -> int:
        """
        Process all documents in a directory with scalable output format.

        Args:
            input_dir: Directory containing input files (.txt, .md, .pdf, .pptx, .docx).
            output_path: Path to output JSONL file.
            file_extensions: File extensions to process.

        Returns:
            Total number of boundaries labeled.
        """
        # Find all supported files
        files = []
        for ext in file_extensions:
            files.extend(input_dir.glob(f"*{ext}"))

        if not files:
            safe_print(f"No files with extensions {file_extensions} found in {input_dir}")
            return 0

        safe_print(f"Found {len(files)} files to process")

        # Calculate total windows for progress tracking
        total_windows = 0
        file_sentences_count = {}

        for file_path in files:
            sentences = self.segmenter.segment_file(file_path)
            file_sentences_count[file_path] = len(sentences)

            if len(sentences) > self.window_manager.window_size:
                n_windows = (len(sentences) - self.window_manager.overlap) // self.window_manager.stride + 1
            else:
                n_windows = 1 if len(sentences) >= 2 else 0

            total_windows += n_windows

        safe_print(f"Total windows to process: {total_windows}")

        # Process all files and collect results with sentences
        all_results: list[tuple[str, list[Sentence], list[BoundaryScore]]] = []

        with tqdm(total=total_windows, desc="Processing windows") as pbar:
            for file_path in files:
                sentences, boundaries = await self.process_document(file_path, pbar)
                doc_id = file_path.stem
                all_results.append((doc_id, sentences, boundaries))

        # Create output directories
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sentences_dir = output_path.parent / "sentences"
        sentences_dir.mkdir(exist_ok=True)

        total_boundaries = 0
        total_sentences = 0

        # Write sentences files (one per document)
        for doc_id, sentences, _ in all_results:
            sentences_file = sentences_dir / f"{doc_id}.json"
            with open(sentences_file, "w", encoding="utf-8") as f:
                json.dump({
                    "doc_id": doc_id,
                    "sentences": [s.text for s in sentences]
                }, f, ensure_ascii=False, indent=2)
            total_sentences += len(sentences)

        # Write boundary scores (indices only, no text duplication)
        with open(output_path, "w", encoding="utf-8") as f:
            for doc_id, sentences, boundaries in all_results:
                for boundary in boundaries:
                    record = {
                        "doc_id": boundary.doc_id,
                        "boundary_idx": boundary.boundary_idx,
                        "sentence_idx_before": boundary.boundary_idx,
                        "sentence_idx_after": boundary.boundary_idx + 1,
                        "score": boundary.score
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    total_boundaries += 1

        safe_print(f"Wrote {total_sentences} sentences to {sentences_dir}/")
        safe_print(f"Wrote {total_boundaries} boundaries to {output_path}")

        return total_boundaries

    async def process_file(
        self,
        input_path: Path,
        output_path: Path
    ) -> int:
        """
        Process a single file with scalable output format.

        Args:
            input_path: Path to input file (.txt, .md, .pdf, .pptx, .docx).
            output_path: Path to output JSONL file.

        Returns:
            Number of boundaries labeled.
        """
        # Calculate windows for progress (handles all supported formats)
        sentences = self.segmenter.segment_file(input_path)

        if len(sentences) > self.window_manager.window_size:
            n_windows = (len(sentences) - self.window_manager.overlap) // self.window_manager.stride + 1
        else:
            n_windows = 1 if len(sentences) >= 2 else 0

        with tqdm(total=n_windows, desc="Processing windows") as pbar:
            sentences, boundaries = await self.process_document(input_path, pbar)

        # Create output directories
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sentences_dir = output_path.parent / "sentences"
        sentences_dir.mkdir(exist_ok=True)

        doc_id = input_path.stem

        # Write sentences file (one per document)
        sentences_file = sentences_dir / f"{doc_id}.json"
        with open(sentences_file, "w", encoding="utf-8") as f:
            json.dump({
                "doc_id": doc_id,
                "sentences": [s.text for s in sentences]
            }, f, ensure_ascii=False, indent=2)

        # Write boundary scores (indices only, no text duplication)
        with open(output_path, "w", encoding="utf-8") as f:
            for boundary in boundaries:
                record = {
                    "doc_id": boundary.doc_id,
                    "boundary_idx": boundary.boundary_idx,
                    "sentence_idx_before": boundary.boundary_idx,
                    "sentence_idx_after": boundary.boundary_idx + 1,
                    "score": boundary.score
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        safe_print(f"Wrote {len(sentences)} sentences to {sentences_file}")
        safe_print(f"Wrote {len(boundaries)} boundaries to {output_path}")

        return len(boundaries)


async def run_pipeline(
    input_path: Path,
    output_path: Path,
    window_size: int = DEFAULT_WINDOW_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    concurrency: int = DEFAULT_CONCURRENCY
) -> int:
    """
    Run the training data generation pipeline.

    Args:
        input_path: Path to input file or directory.
        output_path: Path to output JSONL file.
        window_size: Sentences per window.
        overlap: Overlap between windows.
        concurrency: Max concurrent API calls.

    Returns:
        Number of boundaries labeled.
    """
    pipeline = Pipeline(window_size, overlap, concurrency)

    if input_path.is_dir():
        return await pipeline.process_directory(input_path, output_path)
    else:
        return await pipeline.process_file(input_path, output_path)
