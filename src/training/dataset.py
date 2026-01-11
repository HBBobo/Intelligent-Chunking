"""
Dataset class for boundary scoring training.
"""

import json
from pathlib import Path
from typing import Optional

import torch
from torch.utils.data import Dataset


class BoundaryDataset(Dataset):
    """Dataset for training boundary scoring model."""

    def __init__(
        self,
        boundaries_path: Path,
        sentences_dir: Path,
        tokenizer,
        context_size: int = 5,
        max_length: int = 512,
        doc_ids: Optional[list[str]] = None
    ):
        """
        Initialize the dataset.

        Args:
            boundaries_path: Path to all_output.jsonl file.
            sentences_dir: Path to sentences/*.json directory.
            tokenizer: HuggingFace tokenizer.
            context_size: Number of sentences on each side of boundary.
            max_length: Maximum token length.
            doc_ids: Optional list of doc_ids to include (for train/val/test split).
        """
        self.tokenizer = tokenizer
        self.context_size = context_size
        self.max_length = max_length

        # Load all sentences by doc_id
        self.sentences = {}
        for f in Path(sentences_dir).glob("*.json"):
            with open(f, "r", encoding="utf-8") as file:
                data = json.load(file)
                self.sentences[data["doc_id"]] = data["sentences"]

        # Load boundaries (filter by doc_ids if provided)
        self.boundaries = []
        with open(boundaries_path, "r", encoding="utf-8") as f:
            for line in f:
                boundary = json.loads(line)
                if doc_ids is None or boundary["doc_id"] in doc_ids:
                    # Only include if we have the sentences for this doc
                    if boundary["doc_id"] in self.sentences:
                        self.boundaries.append(boundary)

    def __len__(self) -> int:
        return len(self.boundaries)

    def __getitem__(self, idx: int) -> dict:
        b = self.boundaries[idx]
        sents = self.sentences[b["doc_id"]]
        i = b["boundary_idx"]

        # Get context window: context_size sentences before and after the boundary
        # The boundary is between sentence i and sentence i+1
        left_start = max(0, i - self.context_size + 1)
        left = sents[left_start:i + 1]

        right_end = min(len(sents), i + 1 + self.context_size)
        right = sents[i + 1:right_end]

        # Format: "left context [SEP] right context"
        left_text = " ".join(left)
        right_text = " ".join(right)

        # Use tokenizer's sep_token
        sep = self.tokenizer.sep_token or "[SEP]"
        text = f"{left_text} {sep} {right_text}"

        encoding = self.tokenizer(
            text,
            max_length=self.max_length,
            truncation=True,
            padding="max_length",
            return_tensors="pt"
        )

        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "score": torch.tensor(b["score"], dtype=torch.float),
            "doc_id": b["doc_id"],
            "boundary_idx": b["boundary_idx"]
        }


def get_doc_splits(
    sentences_dir: Path,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    seed: int = 42
) -> tuple[list[str], list[str], list[str]]:
    """
    Split documents into train/val/test sets.

    Args:
        sentences_dir: Path to sentences directory.
        train_ratio: Fraction for training.
        val_ratio: Fraction for validation.
        seed: Random seed for reproducibility.

    Returns:
        Tuple of (train_doc_ids, val_doc_ids, test_doc_ids).
    """
    import random

    # Get all doc_ids
    doc_ids = []
    for f in Path(sentences_dir).glob("*.json"):
        with open(f, "r", encoding="utf-8") as file:
            data = json.load(file)
            doc_ids.append(data["doc_id"])

    # Shuffle with seed
    random.seed(seed)
    random.shuffle(doc_ids)

    # Split
    n = len(doc_ids)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    train_ids = doc_ids[:train_end]
    val_ids = doc_ids[train_end:val_end]
    test_ids = doc_ids[val_end:]

    return train_ids, val_ids, test_ids
