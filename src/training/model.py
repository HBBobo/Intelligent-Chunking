"""
Boundary scoring model architecture.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoModel


class BoundaryScorer(nn.Module):
    """
    Transformer-based boundary scoring model.

    Takes sentence context around a boundary and predicts a score class (0-6).
    Supports both standard classification and ordinal regression (CORN).
    """

    def __init__(
        self,
        model_name: str = "xlm-roberta-base",
        freeze_layers: int = 9,
        dropout: float = 0.3,
        num_classes: int = 7,
        ordinal: bool = False
    ):
        """
        Initialize the model.

        Args:
            model_name: HuggingFace model name.
            freeze_layers: Number of encoder layers to freeze (default 9 of 12).
            dropout: Dropout rate for classification head.
            num_classes: Number of output classes (default 7 for scores 0-6).
            ordinal: If True, use CORN ordinal regression (output num_classes-1 logits).
        """
        super().__init__()

        self.encoder = AutoModel.from_pretrained(model_name)
        self.num_classes = num_classes
        self.ordinal = ordinal
        hidden_size = self.encoder.config.hidden_size  # 768 for base models

        # Optionally freeze early layers
        if freeze_layers > 0:
            # Freeze embeddings
            for param in self.encoder.embeddings.parameters():
                param.requires_grad = False

            # Freeze first N encoder layers
            for layer in self.encoder.encoder.layer[:freeze_layers]:
                for param in layer.parameters():
                    param.requires_grad = False

        # Output dimension: num_classes for classification, num_classes-1 for CORN
        output_dim = num_classes - 1 if ordinal else num_classes

        # Classification/ordinal head
        self.head = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, output_dim)
        )

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor
    ) -> torch.Tensor:
        """
        Forward pass.

        Args:
            input_ids: Token IDs [batch_size, seq_len].
            attention_mask: Attention mask [batch_size, seq_len].

        Returns:
            Logits [batch_size, num_classes].
        """
        outputs = self.encoder(
            input_ids=input_ids,
            attention_mask=attention_mask
        )

        # Use CLS token representation
        cls_token = outputs.last_hidden_state[:, 0, :]

        # Get logits for each class
        logits = self.head(cls_token)

        return logits

    def predict(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor
    ) -> torch.Tensor:
        """
        Predict class labels.

        Args:
            input_ids: Token IDs.
            attention_mask: Attention mask.

        Returns:
            Predicted class indices [batch_size].
        """
        with torch.no_grad():
            logits = self.forward(input_ids, attention_mask)
            if self.ordinal:
                # CORN: count thresholds exceeded
                probs = torch.sigmoid(logits)
                return (probs > 0.5).sum(dim=1)
            else:
                return logits.argmax(dim=-1)

    def predict_expected(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor
    ) -> torch.Tensor:
        """
        Predict expected value (soft prediction using class probabilities).

        This gives smoother predictions than argmax by computing:
        E[score] = sum(prob_i * i) for i in 0..6

        Args:
            input_ids: Token IDs.
            attention_mask: Attention mask.

        Returns:
            Expected scores [batch_size] in range [0, 6].
        """
        with torch.no_grad():
            logits = self.forward(input_ids, attention_mask)
            if self.ordinal:
                # CORN: convert threshold probs to class probs, then expected value
                probs = torch.sigmoid(logits)
                batch_size = logits.size(0)
                # P(Y=k) = P(Y>k-1) - P(Y>k)
                probs_extended = torch.cat([
                    torch.ones(batch_size, 1, device=logits.device),
                    probs,
                    torch.zeros(batch_size, 1, device=logits.device)
                ], dim=1)
                class_probs = (probs_extended[:, :-1] - probs_extended[:, 1:]).clamp(min=0)
                classes = torch.arange(self.num_classes, device=logits.device).float()
                return (class_probs * classes).sum(dim=-1)
            else:
                probs = F.softmax(logits, dim=-1)
                classes = torch.arange(self.num_classes, device=logits.device).float()
                return (probs * classes).sum(dim=-1)
