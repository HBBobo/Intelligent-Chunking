"""
Boundary scoring model architecture.
"""

import torch
import torch.nn as nn
from transformers import AutoModel


class BoundaryScorer(nn.Module):
    """
    Transformer-based boundary scoring model.

    Takes sentence context around a boundary and predicts a score (0-6).
    """

    def __init__(
        self,
        model_name: str = "xlm-roberta-base",
        freeze_layers: int = 0,
        dropout: float = 0.1
    ):
        """
        Initialize the model.

        Args:
            model_name: HuggingFace model name.
            freeze_layers: Number of encoder layers to freeze (0 = none).
            dropout: Dropout rate for regression head.
        """
        super().__init__()

        self.encoder = AutoModel.from_pretrained(model_name)
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

        # Regression head
        self.head = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, 1)
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
            Predicted scores [batch_size] (unbounded, clamp at inference).
        """
        outputs = self.encoder(
            input_ids=input_ids,
            attention_mask=attention_mask
        )

        # Use CLS token representation
        cls_token = outputs.last_hidden_state[:, 0, :]

        # Predict score
        score = self.head(cls_token).squeeze(-1)

        return score

    def predict(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor
    ) -> torch.Tensor:
        """
        Predict scores with clamping to valid range.

        Args:
            input_ids: Token IDs.
            attention_mask: Attention mask.

        Returns:
            Predicted scores clamped to [0, 6].
        """
        with torch.no_grad():
            score = self.forward(input_ids, attention_mask)
            return score.clamp(0, 6)
