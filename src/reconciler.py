"""
Overlap reconciliation for merging boundary scores from multiple windows.
"""

from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class BoundaryScore:
    """A boundary score with metadata."""
    doc_id: str
    boundary_idx: int       # Global index (boundary between sentence i and i+1)
    sentence_before: str
    sentence_after: str
    score: float            # 0-6, half-points allowed (0, 0.5, 1, 1.5, ..., 6)


@dataclass
class EnsembleBoundaryScore:
    """A boundary score with ensemble metadata."""
    doc_id: str
    boundary_idx: int
    sentence_before: str
    sentence_after: str
    score: float                          # Averaged/reconciled score
    gemini_score: Optional[float] = None  # Individual Gemini score
    claude_score: Optional[float] = None  # Individual Claude score
    disagreement: float = 0.0             # abs(gemini - claude)
    flagged: bool = False                 # True if disagreement > threshold


class ScoreReconciler:
    """Reconciles overlapping boundary scores using max strategy."""

    def __init__(self, strategy: str = "max"):
        """
        Initialize the reconciler.

        Args:
            strategy: Reconciliation strategy. Currently only "max" is supported.
        """
        if strategy != "max":
            raise ValueError(f"Unknown strategy: {strategy}. Only 'max' is supported.")
        self.strategy = strategy

    def reconcile(
        self,
        window_scores: list
    ) -> list[Union[BoundaryScore, EnsembleBoundaryScore]]:
        """
        Reconcile scores from multiple windows into final boundary scores.

        Handles both tuple format from BoundaryLabeler and EnsembleScore objects
        from EnsembleLabeler.

        Args:
            window_scores: List of tuples (boundary_idx, score, sent_before, sent_after, doc_id)
                          OR list of EnsembleScore objects.
                          May contain duplicates for overlapping boundaries.

        Returns:
            List of BoundaryScore or EnsembleBoundaryScore objects with unique boundaries.
        """
        if not window_scores:
            return []

        # Detect input type
        first = window_scores[0]
        is_ensemble = hasattr(first, 'boundary_idx')  # EnsembleScore has attrs, tuples don't

        if is_ensemble:
            return self._reconcile_ensemble(window_scores)
        else:
            return self._reconcile_tuples(window_scores)

    def _reconcile_tuples(
        self,
        window_scores: list[tuple[int, float, str, str, str]]
    ) -> list[BoundaryScore]:
        """Reconcile tuple-format scores from BoundaryLabeler."""
        # Group scores by boundary index
        scores_by_boundary: dict[int, list[tuple[float, str, str, str]]] = {}

        for boundary_idx, score, sent_before, sent_after, doc_id in window_scores:
            if boundary_idx not in scores_by_boundary:
                scores_by_boundary[boundary_idx] = []
            scores_by_boundary[boundary_idx].append((score, sent_before, sent_after, doc_id))

        # Reconcile each boundary
        results = []
        for boundary_idx in sorted(scores_by_boundary.keys()):
            scores = scores_by_boundary[boundary_idx]

            # Max strategy: take the highest score
            max_score_tuple = max(scores, key=lambda x: x[0])
            score, sent_before, sent_after, doc_id = max_score_tuple

            results.append(BoundaryScore(
                doc_id=doc_id,
                boundary_idx=boundary_idx,
                sentence_before=sent_before,
                sentence_after=sent_after,
                score=score
            ))

        return results

    def _reconcile_ensemble(
        self,
        window_scores: list
    ) -> list[EnsembleBoundaryScore]:
        """Reconcile EnsembleScore objects from EnsembleLabeler."""
        # Group scores by boundary index
        scores_by_boundary: dict[int, list] = {}

        for es in window_scores:
            if es.boundary_idx not in scores_by_boundary:
                scores_by_boundary[es.boundary_idx] = []
            scores_by_boundary[es.boundary_idx].append(es)

        # Reconcile each boundary
        results = []
        for boundary_idx in sorted(scores_by_boundary.keys()):
            ensemble_scores = scores_by_boundary[boundary_idx]

            # Max strategy: take the EnsembleScore with highest averaged score
            best = max(ensemble_scores, key=lambda x: x.score)

            results.append(EnsembleBoundaryScore(
                doc_id=best.doc_id,
                boundary_idx=boundary_idx,
                sentence_before=best.sentence_before,
                sentence_after=best.sentence_after,
                score=best.score,
                gemini_score=best.gemini_score,
                claude_score=best.claude_score,
                disagreement=best.disagreement,
                flagged=best.flagged
            ))

        return results
