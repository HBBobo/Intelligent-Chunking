"""
Evaluation metrics for boundary scoring model.
"""

from dataclasses import dataclass

import numpy as np
import torch
import torch.nn.functional as F
from scipy.stats import pearsonr, spearmanr
from scipy.spatial.distance import jensenshannon
from sklearn.metrics import mean_squared_error, mean_absolute_error
from torch.utils.data import DataLoader


@dataclass
class ChunkingParams:
    """Parameters for tunable DP chunking algorithm."""
    target_chunk_size: int = 10
    target_coherency: float = 0.6
    min_chunk_size: int = 2
    max_chunk_size: int = 30
    size_weight: float = 0.1
    internal_weight: float = 0.5


CHUNKING_PRESETS = {
    "sections": ChunkingParams(
        target_chunk_size=25,
        target_coherency=0.9,
        min_chunk_size=5,
        max_chunk_size=50
    ),
    "balanced": ChunkingParams(
        target_chunk_size=10,
        target_coherency=0.6,
        min_chunk_size=3,
        max_chunk_size=20
    ),
    "fine": ChunkingParams(
        target_chunk_size=4,
        target_coherency=0.3,
        min_chunk_size=2,
        max_chunk_size=8
    ),
    "rag": ChunkingParams(
        target_chunk_size=8,
        target_coherency=0.65,
        min_chunk_size=3,
        max_chunk_size=15
    )
}


def evaluate(
    model: torch.nn.Module,
    loader: DataLoader,
    device: torch.device,
    num_classes: int = 7
) -> dict:
    """
    Evaluate model on a dataset.

    Args:
        model: The boundary scoring model (outputs logits for 7 classes).
        loader: DataLoader for evaluation data.
        device: Device to run on.
        num_classes: Number of output classes (default 7 for scores 0-6).

    Returns:
        Dictionary with evaluation metrics.
    """
    model.eval()
    preds = []
    targets = []

    with torch.no_grad():
        for batch in loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            scores = batch["score"].numpy()

            # Get logits and convert to expected value (soft prediction)
            logits = model(input_ids, attention_mask)
            probs = F.softmax(logits, dim=-1)
            classes = torch.arange(num_classes, device=device).float()
            pred = (probs * classes).sum(dim=-1).cpu().numpy()

            preds.extend(pred)
            targets.extend(scores)

    preds = np.array(preds)  # Already in [0, 6] range from expected value
    targets = np.array(targets).astype(float)

    # 1. Score correlation vs teacher (Pearson and Spearman)
    pearson_corr = pearsonr(preds, targets)[0]
    spearman_corr = spearmanr(preds, targets)[0]

    # 2. Regression metrics
    mse = mean_squared_error(targets, preds)
    mae = mean_absolute_error(targets, preds)
    rmse = np.sqrt(mse)

    # 3. Histogram similarity
    hist_metrics = compute_histogram_similarity(preds, targets)

    return {
        "pearson": pearson_corr,
        "spearman": spearman_corr,
        "mse": mse,
        "rmse": rmse,
        "mae": mae,
        **hist_metrics
    }


def compute_histogram_similarity(
    preds: np.ndarray,
    targets: np.ndarray,
    bins: np.ndarray = None
) -> dict:
    """
    Compute histogram similarity metrics.

    Args:
        preds: Predicted scores.
        targets: Target scores.
        bins: Histogram bins (default: 0, 0.5, 1, ..., 6.5).

    Returns:
        Dictionary with histogram metrics.
    """
    if bins is None:
        bins = np.arange(0, 7, 0.5)

    # Compute histograms (normalized)
    pred_hist, _ = np.histogram(preds, bins=bins)
    target_hist, _ = np.histogram(targets, bins=bins)

    # Normalize to probability distributions
    pred_dist = pred_hist / pred_hist.sum() if pred_hist.sum() > 0 else pred_hist
    target_dist = target_hist / target_hist.sum() if target_hist.sum() > 0 else target_hist

    # Add small epsilon to avoid division by zero
    eps = 1e-10
    pred_dist = pred_dist + eps
    target_dist = target_dist + eps
    pred_dist = pred_dist / pred_dist.sum()
    target_dist = target_dist / target_dist.sum()

    # Jensen-Shannon divergence (symmetric, bounded [0, 1])
    js_div = jensenshannon(pred_dist, target_dist)

    # Earth Mover's Distance (Wasserstein-1)
    # For 1D histograms, this is the L1 distance between CDFs
    pred_cdf = np.cumsum(pred_dist)
    target_cdf = np.cumsum(target_dist)
    emd = np.sum(np.abs(pred_cdf - target_cdf)) * (bins[1] - bins[0])

    return {
        "js_divergence": js_div,
        "emd": emd,
        "pred_hist": pred_hist.tolist(),
        "target_hist": target_hist.tolist()
    }


def dp_chunk_document(
    sentences: list[str],
    scores: list[float],
    max_chunk_size: int = 10,
    min_chunk_size: int = 2
) -> list[tuple[int, int]]:
    """
    Dynamic programming to find optimal chunk boundaries (legacy version).

    Uses predicted scores as boundary costs (higher score = better split point).
    For more control, use dp_chunk_tunable() instead.

    Args:
        sentences: List of sentences.
        scores: Boundary scores (len = len(sentences) - 1).
        max_chunk_size: Maximum sentences per chunk.
        min_chunk_size: Minimum sentences per chunk.

    Returns:
        List of (start_idx, end_idx) tuples for each chunk.
    """
    n = len(sentences)

    if n <= min_chunk_size:
        return [(0, n)]

    # dp[i] = (min_cost, prev_idx) to chunk sentences[0:i]
    dp = [(float('inf'), -1)] * (n + 1)
    dp[0] = (0.0, -1)

    for i in range(min_chunk_size, n + 1):
        for j in range(max(0, i - max_chunk_size), i - min_chunk_size + 1):
            # Cost of creating chunk [j:i]
            # Use boundary score at position i-1 (between sentence i-1 and i)
            if i < n:
                # Lower score = higher cost to split (we want to split at high scores)
                boundary_cost = 6 - scores[i - 1]
            else:
                # End of document - no cost
                boundary_cost = 0

            total_cost = dp[j][0] + boundary_cost
            if total_cost < dp[i][0]:
                dp[i] = (total_cost, j)

    # Backtrack to get chunks
    chunks = []
    i = n
    while i > 0:
        j = dp[i][1]
        chunks.append((j, i))
        i = j

    return list(reversed(chunks))


def dp_chunk_tunable(
    sentences: list[str],
    scores: list[float],
    params: ChunkingParams = None,
    preset: str = None
) -> list[tuple[int, int]]:
    """
    Tunable DP chunking with coherency and size parameters.

    Cost function components:
    1. Split cost: Quadratic penalty for splitting at low-score boundaries
    2. Size cost: Penalty for deviating from target chunk size
    3. Internal cost: Penalty for high-score boundaries inside chunks

    Args:
        sentences: List of sentences.
        scores: Boundary scores (len = len(sentences) - 1).
        params: ChunkingParams instance. If None, uses preset or defaults.
        preset: Preset name ("sections", "balanced", "fine", "rag").
                Ignored if params is provided.

    Returns:
        List of (start_idx, end_idx) tuples for each chunk.

    Examples:
        # Use preset
        chunks = dp_chunk_tunable(sents, scores, preset="rag")

        # Custom params for large section-based chunks
        chunks = dp_chunk_tunable(sents, scores, ChunkingParams(
            target_chunk_size=20,
            target_coherency=0.9
        ))
    """
    # Resolve parameters
    if params is None:
        if preset and preset in CHUNKING_PRESETS:
            params = CHUNKING_PRESETS[preset]
        else:
            params = ChunkingParams()  # defaults

    n = len(sentences)

    if n <= params.min_chunk_size:
        return [(0, n)]

    threshold = 6 * params.target_coherency

    # dp[i] = (min_cost, prev_idx) to chunk sentences[0:i]
    dp = [(float('inf'), -1)] * (n + 1)
    dp[0] = (0.0, -1)

    for i in range(params.min_chunk_size, n + 1):
        for j in range(max(0, i - params.max_chunk_size),
                       i - params.min_chunk_size + 1):
            chunk_size = i - j

            # 1. Split cost (quadratic penalty for low-score boundaries)
            if i < n:
                score = scores[i - 1]
                if score >= threshold:
                    split_cost = 0  # Free split at good boundaries
                else:
                    split_cost = (threshold - score) ** 2
            else:
                split_cost = 0  # End of document

            # 2. Size cost (penalty for deviating from target)
            size_diff = abs(chunk_size - params.target_chunk_size)
            size_cost = params.size_weight * (size_diff ** 2)

            # 3. Internal coherence cost (penalty for missed split points)
            internal_cost = 0
            if params.internal_weight > 0:
                for k in range(j, i - 1):
                    if k < len(scores):
                        excess = max(0, scores[k] - threshold)
                        internal_cost += params.internal_weight * excess

            total_cost = dp[j][0] + split_cost + size_cost + internal_cost

            if total_cost < dp[i][0]:
                dp[i] = (total_cost, j)

    # Backtrack to get chunks
    chunks = []
    i = n
    while i > 0:
        j = dp[i][1]
        chunks.append((j, i))
        i = j

    return list(reversed(chunks))


def chunk_document(
    sentences: list[str],
    scores: list[float],
    preset: str = "balanced"
) -> list[tuple[int, int]]:
    """
    Convenience function to chunk with a preset configuration.

    Args:
        sentences: List of sentences.
        scores: Boundary scores.
        preset: One of "sections", "balanced", "fine", "rag".

    Returns:
        List of (start_idx, end_idx) tuples.
    """
    return dp_chunk_tunable(sentences, scores, preset=preset)


def format_chunks_for_display(
    sentences: list[str],
    chunks: list[tuple[int, int]],
    scores: list[float] = None
) -> str:
    """
    Format chunks for human inspection.

    Args:
        sentences: List of sentences.
        chunks: List of (start, end) tuples.
        scores: Optional boundary scores for display.

    Returns:
        Formatted string for display.
    """
    output = []

    for i, (start, end) in enumerate(chunks):
        output.append(f"\n{'='*60}")
        output.append(f"CHUNK {i+1} (sentences {start+1}-{end})")
        output.append('='*60)

        for j in range(start, end):
            sent = sentences[j]
            # Truncate long sentences
            if len(sent) > 100:
                sent = sent[:100] + "..."
            output.append(f"  [{j+1}] {sent}")

            # Show boundary score after this sentence (if not last in chunk)
            if scores and j < end - 1 and j < len(scores):
                output.append(f"      └─ score: {scores[j]:.1f}")

        # Show boundary score at chunk end
        if scores and end - 1 < len(scores):
            output.append(f"  ── SPLIT (score: {scores[end-1]:.1f}) ──")

    return "\n".join(output)
