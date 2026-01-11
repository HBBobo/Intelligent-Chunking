"""
Overlap reconciliation for merging boundary scores from multiple windows.
"""

from dataclasses import dataclass


@dataclass
class BoundaryScore:
    """A boundary score with metadata."""
    doc_id: str
    boundary_idx: int       # Global index (boundary between sentence i and i+1)
    sentence_before: str
    sentence_after: str
    score: float            # 0-6, half-points allowed (0, 0.5, 1, 1.5, ..., 6)


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
        window_scores: list[tuple[int, int, str, str, int]]
    ) -> list[BoundaryScore]:
        """
        Reconcile scores from multiple windows into final boundary scores.

        Args:
            window_scores: List of tuples (boundary_idx, score, sent_before, sent_after, doc_id).
                          May contain duplicates for overlapping boundaries.

        Returns:
            List of BoundaryScore objects with unique boundaries.
        """
        # Group scores by boundary index
        scores_by_boundary: dict[int, list[tuple[int, str, str, str]]] = {}

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
