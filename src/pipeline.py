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


def normalize_text(text: str) -> str:
    """
    Normalize text by ensuring it's valid UTF-8.

    Handles Windows-1252 encoded characters that may appear in DOCX files
    or Windows filenames.
    """
    # If text is already valid UTF-8, try to detect and fix Windows-1252 chars
    try:
        # First, try to encode as latin-1 (which accepts all bytes 0-255)
        # and then decode as cp1252 (Windows-1252) to get proper Unicode
        # This handles cases where strings contain raw Windows-1252 bytes
        encoded = text.encode('latin-1')
        text = encoded.decode('cp1252', errors='replace')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass

    # Ensure the result is valid UTF-8 by encoding and decoding
    try:
        text = text.encode('utf-8', errors='replace').decode('utf-8')
    except Exception:
        pass

    return text


from .config import (
    DEFAULT_WINDOW_SIZE,
    DEFAULT_OVERLAP,
    DEFAULT_CONCURRENCY,
    CLAUDE_CONCURRENCY,
    CLAUDE_API_KEY,
    USE_ENSEMBLE,
    DISAGREEMENT_THRESHOLD,
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
        self.reconciler = ScoreReconciler(strategy="max")

        # Use ensemble labeler if Claude API key is available and ensemble mode is enabled
        self.use_ensemble = False
        if USE_ENSEMBLE and CLAUDE_API_KEY:
            try:
                from .claude_client import ClaudeClient
                from .ensemble_labeler import EnsembleLabeler
                self.claude_client = ClaudeClient(CLAUDE_CONCURRENCY)
                self.labeler = EnsembleLabeler(
                    self.client,
                    self.claude_client,
                    disagreement_threshold=DISAGREEMENT_THRESHOLD
                )
                self.use_ensemble = True
                safe_print("Ensemble mode enabled (Gemini + Claude)")
            except Exception as e:
                safe_print(f"Warning: Could not initialize ensemble mode: {e}")
                safe_print("Falling back to Gemini-only mode")
                self.labeler = BoundaryLabeler(self.client)
        else:
            self.labeler = BoundaryLabeler(self.client)
            if USE_ENSEMBLE and not CLAUDE_API_KEY:
                safe_print("Note: Ensemble mode requested but CLAUDE_API_KEY not set. Using Gemini only.")

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

        # Normalize doc_id to handle filename encoding issues
        doc_id = normalize_text(file_path.stem)

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

    def _get_unique_doc_id(self, file_path: Path, input_dir: Path) -> str:
        """
        Generate unique doc_id, handling duplicates in subfolders.

        If file is in a subfolder, prefixes with parent folder names to ensure uniqueness.
        E.g., biology/chapter1/intro.pdf -> biology_chapter1_intro

        Args:
            file_path: Path to the file.
            input_dir: Base input directory.

        Returns:
            Unique document ID string.
        """
        base_name = normalize_text(file_path.stem)

        # Get relative path from input_dir
        try:
            rel_path = file_path.relative_to(input_dir)
        except ValueError:
            # file_path is not under input_dir, just use stem
            return base_name

        # If file is in a subfolder, prefix with parent folder names
        if len(rel_path.parts) > 1:
            # e.g., "biology/chapter1/intro.pdf" -> "biology_chapter1_intro"
            prefix = "_".join(rel_path.parts[:-1])
            return f"{prefix}_{base_name}"

        return base_name

    async def process_directory(
        self,
        input_dir: Path,
        output_path: Path,
        file_extensions: tuple[str, ...] = (".txt", ".md", ".pdf", ".pptx", ".docx"),
        recursive: bool = True
    ) -> int:
        """
        Process all documents in a directory with scalable output format.

        Args:
            input_dir: Directory containing input files (.txt, .md, .pdf, .pptx, .docx).
            output_path: Path to output JSONL file.
            file_extensions: File extensions to process.
            recursive: If True, search subfolders recursively. Default True.

        Returns:
            Total number of boundaries labeled.
        """
        # Find all supported files (recursive or non-recursive)
        files = []
        for ext in file_extensions:
            if recursive:
                files.extend(input_dir.rglob(f"*{ext}"))  # Recursive
            else:
                files.extend(input_dir.glob(f"*{ext}"))   # Non-recursive

        # Sort and deduplicate for consistent ordering
        files = sorted(set(files))

        if not files:
            safe_print(f"No files with extensions {file_extensions} found in {input_dir}")
            return 0

        safe_print(f"Found {len(files)} files to process")

        # Show folder breakdown when recursive
        if recursive and len(files) > 1:
            folders = set()
            for f in files:
                try:
                    rel = f.relative_to(input_dir).parent
                    folders.add(str(rel) if str(rel) != "." else "(root)")
                except ValueError:
                    pass
            if len(folders) > 1:
                folder_list = sorted(folders)[:5]
                more = f"... and {len(folders) - 5} more" if len(folders) > 5 else ""
                safe_print(f"  From {len(folders)} folder(s): {folder_list} {more}")

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
        # Tuple: (doc_id, file_path, sentences, boundaries)
        all_results: list[tuple[str, Path, list[Sentence], list]] = []

        with tqdm(total=total_windows, desc="Processing windows") as pbar:
            for file_path in files:
                sentences, boundaries = await self.process_document(file_path, pbar)
                # Use unique doc_id that handles subfolders
                doc_id = self._get_unique_doc_id(file_path, input_dir)
                all_results.append((doc_id, file_path, sentences, boundaries))

        # Create output directories
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sentences_dir = output_path.parent / "sentences"
        sentences_dir.mkdir(exist_ok=True)

        total_boundaries = 0
        total_sentences = 0

        # Write sentences files (one per document)
        for doc_id, file_path, sentences, _ in all_results:
            sentences_file = sentences_dir / f"{doc_id}.json"
            # Normalize text to handle any encoding issues
            normalized_sentences = [normalize_text(s.text) for s in sentences]
            with open(sentences_file, "w", encoding="utf-8") as f:
                json.dump({
                    "doc_id": doc_id,
                    "sentences": normalized_sentences
                }, f, ensure_ascii=False, indent=2)
            total_sentences += len(sentences)

        # Write boundary scores (with ensemble metadata if available)
        flagged_count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for doc_id, file_path, sentences, boundaries in all_results:
                for boundary in boundaries:
                    record = {
                        "doc_id": doc_id,  # Use our unique doc_id
                        "boundary_idx": boundary.boundary_idx,
                        "sentence_idx_before": boundary.boundary_idx,
                        "sentence_idx_after": boundary.boundary_idx + 1,
                        "score": boundary.score
                    }
                    # Add ensemble metadata if available (EnsembleScore has these attrs)
                    if hasattr(boundary, 'gemini_score'):
                        record["gemini_score"] = boundary.gemini_score
                    if hasattr(boundary, 'claude_score'):
                        record["claude_score"] = boundary.claude_score
                    if hasattr(boundary, 'disagreement'):
                        record["disagreement"] = boundary.disagreement
                    if hasattr(boundary, 'flagged'):
                        record["flagged"] = boundary.flagged
                        if boundary.flagged:
                            flagged_count += 1
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    total_boundaries += 1

        safe_print(f"Wrote {total_sentences} sentences to {sentences_dir}/")
        safe_print(f"Wrote {total_boundaries} boundaries to {output_path}")
        if self.use_ensemble and flagged_count > 0:
            safe_print(f"  ({flagged_count} boundaries flagged for disagreement > {DISAGREEMENT_THRESHOLD})")

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

        # Normalize doc_id to handle filename encoding issues
        doc_id = normalize_text(input_path.stem)

        # Write sentences file (one per document)
        sentences_file = sentences_dir / f"{doc_id}.json"
        # Normalize text to handle any encoding issues
        normalized_sentences = [normalize_text(s.text) for s in sentences]
        with open(sentences_file, "w", encoding="utf-8") as f:
            json.dump({
                "doc_id": doc_id,
                "sentences": normalized_sentences
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
