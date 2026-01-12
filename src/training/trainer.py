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


class CORNLoss(nn.Module):
    """
    CORN (Conditional Ordinal Regression Network) Loss.

    Converts K-class ordinal regression into K-1 binary classification tasks.
    For scores 0-6, we have 6 binary tasks: "Is score > k?" for k=0,1,2,3,4,5.

    This respects ordinal structure: predicting 2 when true=3 is better than
    predicting 0 when true=3 (unlike standard classification).

    Reference: https://arxiv.org/abs/2111.08851
    """

    def __init__(self, num_classes: int = 7):
        super().__init__()
        self.num_classes = num_classes

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Args:
            logits: [batch_size, num_classes-1] raw outputs for each threshold.
            targets: [batch_size] integer class labels 0 to num_classes-1.

        Returns:
            CORN loss value.
        """
        batch_size = logits.size(0)
        num_thresholds = self.num_classes - 1  # 6 for 7 classes

        # Create binary targets for each threshold
        # For target=3: binary_targets = [1, 1, 1, 0, 0, 0] (score > 0,1,2 but not > 3,4,5)
        thresholds = torch.arange(num_thresholds, device=logits.device).unsqueeze(0)
        targets_expanded = targets.unsqueeze(1)
        binary_targets = (targets_expanded > thresholds).float()

        # Binary cross entropy for each threshold
        loss = nn.functional.binary_cross_entropy_with_logits(
            logits, binary_targets, reduction='mean'
        )

        return loss


def corn_predict(logits: torch.Tensor) -> torch.Tensor:
    """
    Convert CORN logits to predicted class labels.

    Args:
        logits: [batch_size, num_classes-1] threshold logits.

    Returns:
        [batch_size] predicted class labels.
    """
    probs = torch.sigmoid(logits)
    # Predicted class = number of thresholds exceeded
    # If probs = [0.9, 0.8, 0.6, 0.3, 0.1, 0.05], we predict class 3
    # (exceeded thresholds 0, 1, 2 but not 3, 4, 5)
    predicted = (probs > 0.5).sum(dim=1)
    return predicted


def corn_predict_proba(logits: torch.Tensor) -> torch.Tensor:
    """
    Convert CORN logits to class probabilities.

    Args:
        logits: [batch_size, num_classes-1] threshold logits.

    Returns:
        [batch_size, num_classes] class probabilities.
    """
    probs = torch.sigmoid(logits)
    num_classes = logits.size(1) + 1
    batch_size = logits.size(0)

    # P(Y=k) = P(Y>k-1) - P(Y>k)
    # Add boundaries: P(Y>-1) = 1, P(Y>K-1) = 0
    probs_extended = torch.cat([
        torch.ones(batch_size, 1, device=logits.device),
        probs,
        torch.zeros(batch_size, 1, device=logits.device)
    ], dim=1)

    class_probs = probs_extended[:, :-1] - probs_extended[:, 1:]
    return class_probs.clamp(min=0)  # Ensure non-negative


def corn_expected_value(logits: torch.Tensor) -> torch.Tensor:
    """
    Convert CORN logits to expected value (soft prediction).

    Args:
        logits: [batch_size, num_classes-1] threshold logits.

    Returns:
        [batch_size] expected values.
    """
    class_probs = corn_predict_proba(logits)
    num_classes = class_probs.size(1)
    classes = torch.arange(num_classes, device=logits.device).float()
    return (class_probs * classes).sum(dim=1)


class FocalLoss(nn.Module):
    """
    Focal Loss for handling class imbalance and hard examples.

    Focal loss down-weights easy examples and focuses on hard ones,
    which helps prevent the model from collapsing to predict the mean.

    FL(p_t) = -alpha * (1 - p_t)^gamma * log(p_t)

    Args:
        alpha: Weighting factor for rare classes (can be tensor of per-class weights).
        gamma: Focusing parameter. Higher gamma = more focus on hard examples.
               gamma=0 is equivalent to CrossEntropyLoss.
               gamma=2 is typical for moderate class imbalance.
        label_smoothing: Label smoothing factor (0.0-1.0). Prevents overconfidence.
        reduction: 'mean', 'sum', or 'none'.
    """

    def __init__(
        self,
        alpha: torch.Tensor = None,
        gamma: float = 2.0,
        label_smoothing: float = 0.0,
        reduction: str = 'mean'
    ):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.label_smoothing = label_smoothing
        self.reduction = reduction

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Args:
            logits: [batch_size, num_classes] raw model outputs.
            targets: [batch_size] integer class labels.

        Returns:
            Focal loss value.
        """
        # Apply label smoothing to cross entropy
        ce_loss = nn.functional.cross_entropy(
            logits, targets,
            reduction='none',
            label_smoothing=self.label_smoothing
        )

        # For focal weighting, use unsmoothed probabilities
        probs = torch.softmax(logits, dim=-1)
        pt = probs.gather(1, targets.unsqueeze(1)).squeeze(1)  # p_t = probability of correct class

        focal_weight = (1 - pt) ** self.gamma

        # Apply per-class alpha weighting if provided
        if self.alpha is not None:
            alpha = self.alpha.to(logits.device)
            alpha_t = alpha[targets]
            focal_weight = alpha_t * focal_weight

        focal_loss = focal_weight * ce_loss

        if self.reduction == 'mean':
            return focal_loss.mean()
        elif self.reduction == 'sum':
            return focal_loss.sum()
        else:
            return focal_loss


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
    class_weights: torch.Tensor = None,
    use_focal_loss: bool = False,
    focal_gamma: float = 2.0
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
        use_focal_loss: If True, use FocalLoss instead of CrossEntropyLoss.
        focal_gamma: Focusing parameter for FocalLoss (default 2.0).

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

    # Select loss function
    if use_focal_loss:
        alpha = class_weights.to(device) if class_weights is not None else None
        criterion = FocalLoss(alpha=alpha, gamma=focal_gamma)
        print(f"Using FocalLoss with gamma={focal_gamma}, alpha={alpha.tolist() if alpha is not None else None}")
    elif class_weights is not None:
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
