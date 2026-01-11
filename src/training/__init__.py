"""
Training module for boundary scoring model.
"""

from .dataset import BoundaryDataset, get_doc_splits
from .model import BoundaryScorer
from .trainer import train, save_model, load_model
from .evaluate import evaluate, dp_chunk_document, format_chunks_for_display

__all__ = [
    "BoundaryDataset",
    "get_doc_splits",
    "BoundaryScorer",
    "train",
    "save_model",
    "load_model",
    "evaluate",
    "dp_chunk_document",
    "format_chunks_for_display"
]
