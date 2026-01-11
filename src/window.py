"""
Window management for overlapping sentence windows.
"""

from dataclasses import dataclass
from .segmenter import Sentence


@dataclass
class Window:
    """A window of sentences with metadata for reconciliation."""
    sentences: list[Sentence]
    start_idx: int          # Global index of first sentence
    end_idx: int            # Global index of last sentence (inclusive)
    window_id: int          # Window number within document
    doc_id: str             # Document identifier


class WindowManager:
    """Creates and manages overlapping windows of sentences."""

    def __init__(self, window_size: int = 100, overlap: int = 20):
        """
        Initialize the window manager.

        Args:
            window_size: Number of sentences per window.
            overlap: Number of overlapping sentences between adjacent windows.
        """
        if overlap >= window_size:
            raise ValueError("Overlap must be less than window size")

        self.window_size = window_size
        self.overlap = overlap
        self.stride = window_size - overlap

    def create_windows(self, sentences: list[Sentence], doc_id: str) -> list[Window]:
        """
        Create overlapping windows from a list of sentences.

        Args:
            sentences: List of sentences from a document.
            doc_id: Identifier for the document.

        Returns:
            List of Window objects.
        """
        if not sentences:
            return []

        windows = []
        n_sentences = len(sentences)

        # Handle documents smaller than window size
        if n_sentences <= self.window_size:
            windows.append(Window(
                sentences=sentences,
                start_idx=0,
                end_idx=n_sentences - 1,
                window_id=0,
                doc_id=doc_id
            ))
            return windows

        # Create overlapping windows
        start = 0
        window_id = 0

        while start < n_sentences:
            end = min(start + self.window_size, n_sentences)
            window_sentences = sentences[start:end]

            windows.append(Window(
                sentences=window_sentences,
                start_idx=start,
                end_idx=end - 1,
                window_id=window_id,
                doc_id=doc_id
            ))

            # Move to next window
            start += self.stride
            window_id += 1

            # If remaining sentences are less than overlap, include them in last window
            if start < n_sentences and n_sentences - start < self.overlap:
                break

        return windows

    def get_boundary_indices(self, window: Window) -> list[int]:
        """
        Get global boundary indices for a window.

        A boundary at index i means the boundary between sentence i and i+1.

        Args:
            window: The window to get boundary indices for.

        Returns:
            List of global boundary indices.
        """
        # Boundaries are between consecutive sentences
        # For n sentences, there are n-1 boundaries
        return list(range(window.start_idx, window.end_idx))
