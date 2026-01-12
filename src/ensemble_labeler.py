"""
Multi-model ensemble labeling with score aggregation.

Uses multiple LLM teachers (Gemini + Claude) and averages their scores
to reduce individual model bias and produce more reliable labels.
"""

import sys
import asyncio
from dataclasses import dataclass
from typing import Optional

from .window import Window
from .gemini_client import GeminiClient
from .config import PROMPT_TEMPLATE_PATH


def safe_print(msg: str = "") -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg, file=sys.stderr)
    except UnicodeEncodeError:
        encoding = sys.stderr.encoding or 'utf-8'
        print(msg.encode(encoding, errors='replace').decode(encoding), file=sys.stderr)


@dataclass
class EnsembleScore:
    """Score with individual model outputs and agreement metric."""
    boundary_idx: int
    score: float                          # Averaged score
    gemini_score: Optional[float]         # Individual Gemini score
    claude_score: Optional[float]         # Individual Claude score
    disagreement: float                   # abs(gemini - claude)
    sentence_before: str
    sentence_after: str
    doc_id: str
    flagged: bool = False                 # True if disagreement > threshold


class EnsembleLabeler:
    """Labels boundaries using multiple models and aggregates scores."""

    def __init__(
        self,
        gemini_client: GeminiClient,
        claude_client: Optional["ClaudeClient"] = None,
        prompt_template: str = None,
        disagreement_threshold: float = 2.0
    ):
        """
        Initialize the ensemble labeler.

        Args:
            gemini_client: The Gemini API client.
            claude_client: Optional Claude API client. If None, uses Gemini only.
            prompt_template: Optional custom prompt template.
            disagreement_threshold: Flag boundaries with disagreement > this value.
        """
        self.gemini = gemini_client
        self.claude = claude_client
        self.disagreement_threshold = disagreement_threshold

        # Load prompt template
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
    ) -> list[EnsembleScore] | None:
        """
        Label all boundaries in a window using all available models.

        Args:
            window: The window to label.

        Returns:
            List of EnsembleScore objects, or None if labeling fails completely.
        """
        if len(window.sentences) < 2:
            return []

        prompt = self.build_prompt(window)
        expected_count = len(window.sentences) - 1

        # Run models in parallel
        tasks = [self.gemini.generate_boundary_scores(prompt)]
        if self.claude is not None:
            tasks.append(self.claude.generate_boundary_scores(prompt))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Extract results, handling exceptions
        gemini_anchored = None
        claude_anchored = None

        if not isinstance(results[0], Exception):
            gemini_anchored = results[0]
        else:
            safe_print(f"Warning: Gemini failed for window {window.window_id}: {results[0]}")

        if len(results) > 1:
            if not isinstance(results[1], Exception):
                claude_anchored = results[1]
            else:
                safe_print(f"Warning: Claude failed for window {window.window_id}: {results[1]}")

        # Validate score arrays
        gemini_validated = self._validate_scores(
            gemini_anchored, expected_count, "Gemini", window.window_id
        )
        claude_validated = self._validate_scores(
            claude_anchored, expected_count, "Claude", window.window_id
        )

        if gemini_validated is None and claude_validated is None:
            safe_print(f"Warning: All models failed for window {window.window_id} "
                       f"of document '{window.doc_id}'")
            return None

        # Aggregate scores
        return self._aggregate_scores(window, gemini_validated, claude_validated)

    def _validate_scores(
        self,
        scores: list[float] | None,
        expected_count: int,
        model_name: str,
        window_id: int
    ) -> dict[int, float] | None:
        """
        Validate score array and return a dict mapping boundary index to score.

        Args:
            scores: List of float scores from model
            expected_count: Expected number of boundaries
            model_name: Name for logging
            window_id: Window ID for logging

        Returns:
            Dict mapping boundary index to score, or None if validation fails
        """
        if scores is None:
            return None

        # Check array length matches expected
        if len(scores) != expected_count:
            safe_print(f"Warning: {model_name} returned {len(scores)} scores, "
                       f"expected {expected_count} for window {window_id}")
            return None

        # Convert to dict mapping index -> score
        return {i: scores[i] for i in range(len(scores))}

    def _aggregate_scores(
        self,
        window: Window,
        gemini_scores: dict[int, float] | None,
        claude_scores: dict[int, float] | None
    ) -> list[EnsembleScore]:
        """Aggregate scores from multiple models."""
        results = []
        n = len(window.sentences) - 1

        for i in range(n):
            g_score = gemini_scores.get(i) if gemini_scores else None
            c_score = claude_scores.get(i) if claude_scores else None

            # Compute average (handle missing)
            valid_scores = [s for s in [g_score, c_score] if s is not None]
            if not valid_scores:
                continue

            avg_score = sum(valid_scores) / len(valid_scores)

            # Compute disagreement
            if g_score is not None and c_score is not None:
                disagreement = abs(g_score - c_score)
            else:
                disagreement = 0.0  # Single model, no disagreement

            results.append(EnsembleScore(
                boundary_idx=window.start_idx + i,
                score=avg_score,
                gemini_score=g_score,
                claude_score=c_score,
                disagreement=disagreement,
                sentence_before=window.sentences[i].text,
                sentence_after=window.sentences[i + 1].text,
                doc_id=window.doc_id,
                flagged=disagreement > self.disagreement_threshold
            ))

        return results
