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
    Uses classification instead of regression for better handling of discrete scores.
    """

    def __init__(
        self,
        model_name: str = "xlm-roberta-base",
        freeze_layers: int = 9,
        dropout: float = 0.3,
        num_classes: int = 7
    ):
        """
        Initialize the model.

        Args:
            model_name: HuggingFace model name.
            freeze_layers: Number of encoder layers to freeze (default 9 of 12).
            dropout: Dropout rate for classification head.
            num_classes: Number of output classes (default 7 for scores 0-6).
        """
        super().__init__()

        self.encoder = AutoModel.from_pretrained(model_name)
        self.num_classes = num_classes
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

        # Classification head (7 classes for scores 0-6)
        self.head = nn.Sequential(
            nn.Linear(hidden_size, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, num_classes)
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
        Predict class labels (argmax).

        Args:
            input_ids: Token IDs.
            attention_mask: Attention mask.

        Returns:
            Predicted class indices [batch_size].
        """
        with torch.no_grad():
            logits = self.forward(input_ids, attention_mask)
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
            probs = F.softmax(logits, dim=-1)
            classes = torch.arange(self.num_classes, device=logits.device).float()
            return (probs * classes).sum(dim=-1)
