"""
Prompt construction and response parsing for boundary labeling.
"""

import sys
from pathlib import Path

from .config import PROMPT_TEMPLATE_PATH
from .window import Window
from .gemini_client import GeminiClient


def safe_print(msg: str) -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode())


class BoundaryLabeler:
    """Constructs prompts and labels boundaries using Gemini."""

    def __init__(self, client: GeminiClient, prompt_template: str | None = None):
        """
        Initialize the labeler.

        Args:
            client: The Gemini client to use for API calls.
            prompt_template: Optional custom prompt template.
                           If None, loads from config/prompt_template.txt.
        """
        self.client = client

        if prompt_template is None:
            if PROMPT_TEMPLATE_PATH.exists():
                self.prompt_template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
            else:
                raise FileNotFoundError(
                    f"Prompt template not found at {PROMPT_TEMPLATE_PATH}"
                )
        else:
            self.prompt_template = prompt_template

    def build_prompt(self, window: Window) -> str:
        """
        Build a prompt for labeling boundaries in a window.

        Args:
            window: The window of sentences to label.

        Returns:
            The formatted prompt string.
        """
        # Format sentences with numbers
        numbered_sentences = "\n".join(
            f"{i + 1}. {sent.text}"
            for i, sent in enumerate(window.sentences)
        )

        # Number of boundaries = number of sentences - 1
        n_boundaries = len(window.sentences) - 1

        return self.prompt_template.format(
            numbered_sentences=numbered_sentences,
            n_boundaries=n_boundaries
        )

    async def label_window(
        self,
        window: Window
    ) -> list[tuple[int, float, str, str, str]] | None:
        """
        Label all boundaries in a window.

        Args:
            window: The window to label.

        Returns:
            List of (boundary_idx, score, sent_before, sent_after, doc_id) tuples,
            or None if labeling fails. Score is a float (0-6, half-points allowed).
        """
        if len(window.sentences) < 2:
            return []

        prompt = self.build_prompt(window)
        expected_count = len(window.sentences) - 1

        scores = await self.client.generate_boundary_scores(prompt)

        if scores is None:
            safe_print(f"Warning: Failed to get scores for window {window.window_id} "
                       f"of document '{window.doc_id}'")
            return None

        # Handle off-by-one errors (common LLM mistake)
        if len(scores) == expected_count + 1:
            safe_print(f"Note: Got {len(scores)} scores, expected {expected_count}. Truncating.")
            scores = scores[:expected_count]
        elif len(scores) == expected_count - 1:
            # One fewer than expected - pad with 1.0 (safe continuation default)
            safe_print(f"Note: Got {len(scores)} scores, expected {expected_count}. Padding with 1.")
            scores = scores + [1.0]
        elif len(scores) != expected_count:
            safe_print(f"Warning: Expected {expected_count} scores but got {len(scores)} "
                       f"for window {window.window_id} of document '{window.doc_id}'")
            return None

        # Build result tuples
        results = []
        for i, score in enumerate(scores):
            boundary_idx = window.start_idx + i
            sent_before = window.sentences[i].text
            sent_after = window.sentences[i + 1].text

            results.append((
                boundary_idx,
                score,
                sent_before,
                sent_after,
                window.doc_id
            ))

        return results
