"""
Training loop for boundary scoring model.
"""

import json
from pathlib import Path
from collections import Counter

import torch
import torch.nn as nn
from torch.optim import AdamW
from torch.utils.data import DataLoader
from transformers import get_linear_schedule_with_warmup
from tqdm.auto import tqdm

from .evaluate import evaluate


def compute_class_weights(boundaries_path: Path, num_classes: int = 7) -> torch.Tensor:
    """
    Compute inverse frequency class weights for handling imbalanced data.

    Args:
        boundaries_path: Path to all_training_data.jsonl file.
        num_classes: Number of score classes (default 7 for 0-6).

    Returns:
        Tensor of class weights [num_classes].
    """
    counts = Counter()
    with open(boundaries_path, "r", encoding="utf-8") as f:
        for line in f:
            boundary = json.loads(line)
            counts[boundary["score"]] += 1

    total = sum(counts.values())
    weights = []
    for i in range(num_classes):
        count = counts.get(i, 1)  # Avoid division by zero
        # Inverse frequency weighting
        weights.append(total / (num_classes * count))

    return torch.tensor(weights, dtype=torch.float32)


def train(
    model: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: torch.device,
    epochs: int = 3,
    lr: float = 2e-5,
    weight_decay: float = 0.01,
    warmup_ratio: float = 0.1,
    patience: int = 2,
    class_weights: torch.Tensor = None
) -> dict:
    """
    Train the boundary scoring model.

    Args:
        model: The model to train.
        train_loader: Training data loader.
        val_loader: Validation data loader.
        device: Device to train on.
        epochs: Number of epochs.
        lr: Learning rate.
        weight_decay: Weight decay for AdamW.
        warmup_ratio: Fraction of steps for warmup.
        patience: Early stopping patience (epochs without improvement).
        class_weights: Optional tensor of class weights for imbalanced data.

    Returns:
        Dictionary with training history.
    """
    model = model.to(device)

    optimizer = AdamW(
        model.parameters(),
        lr=lr,
        weight_decay=weight_decay
    )

    total_steps = len(train_loader) * epochs
    warmup_steps = int(total_steps * warmup_ratio)

    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_steps
    )

    # Use weighted loss if class_weights provided
    if class_weights is not None:
        class_weights = class_weights.to(device)
        criterion = nn.CrossEntropyLoss(weight=class_weights)
        print(f"Using weighted CrossEntropyLoss with weights: {class_weights.tolist()}")
    else:
        criterion = nn.CrossEntropyLoss()

    history = {
        "train_loss": [],
        "val_loss": [],
        "val_pearson": [],
        "val_mae": []
    }

    best_val_loss = float('inf')
    best_model_state = None
    epochs_without_improvement = 0

    for epoch in range(epochs):
        # Training
        model.train()
        train_losses = []

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}")
        for batch in pbar:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            scores = batch["score"].to(device)

            optimizer.zero_grad()

            pred = model(input_ids, attention_mask)
            loss = criterion(pred, scores)

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            train_losses.append(loss.item())
            pbar.set_postfix({"loss": f"{loss.item():.4f}"})

        avg_train_loss = sum(train_losses) / len(train_losses)
        history["train_loss"].append(avg_train_loss)

        # Validation
        val_metrics = evaluate(model, val_loader, device)
        val_loss = val_metrics["mse"]

        history["val_loss"].append(val_loss)
        history["val_pearson"].append(val_metrics["pearson"])
        history["val_mae"].append(val_metrics["mae"])

        print(f"\nEpoch {epoch+1}: "
              f"train_loss={avg_train_loss:.4f}, "
              f"val_loss={val_loss:.4f}, "
              f"val_pearson={val_metrics['pearson']:.4f}, "
              f"val_mae={val_metrics['mae']:.4f}")

        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_model_state = model.state_dict().copy()
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= patience:
                print(f"\nEarly stopping after {epoch+1} epochs")
                break

    # Restore best model
    if best_model_state is not None:
        model.load_state_dict(best_model_state)

    return history


def save_model(model: nn.Module, path: str, tokenizer=None):
    """
    Save model and optionally tokenizer.

    Args:
        model: The trained model.
        path: Path to save directory.
        tokenizer: Optional tokenizer to save.
    """
    import os
    os.makedirs(path, exist_ok=True)

    # Save model
    torch.save(model.state_dict(), f"{path}/model.pt")

    # Save tokenizer if provided
    if tokenizer is not None:
        tokenizer.save_pretrained(path)

    print(f"Model saved to {path}")


def load_model(path: str, model_name: str = "xlm-roberta-base") -> nn.Module:
    """
    Load a trained model.

    Args:
        path: Path to saved model directory.
        model_name: Base model name for architecture.

    Returns:
        Loaded model.
    """
    from .model import BoundaryScorer

    model = BoundaryScorer(model_name=model_name)
    model.load_state_dict(torch.load(f"{path}/model.pt"))
    model.eval()

    return model
