"""
Async Claude API client with rate limiting and retry logic.
"""

import asyncio
import json
import sys
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

try:
    import anthropic
except ImportError:
    anthropic = None

from .config import (
    CLAUDE_API_KEY,
    CLAUDE_MODEL,
    MAX_RETRIES
)


def safe_print(msg: str = "") -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode())


class ClaudeClient:
    """Async wrapper for Claude API with rate limiting."""

    def __init__(self, concurrency: int = 5):
        """
        Initialize the Claude client.

        Args:
            concurrency: Maximum number of concurrent API calls.
        """
        if anthropic is None:
            raise ImportError(
                "anthropic package not installed. "
                "Install it with: pip install anthropic"
            )

        if not CLAUDE_API_KEY:
            raise ValueError(
                "CLAUDE_API_KEY not found in environment. "
                "Please set it in your .env file."
            )

        self.client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        self.semaphore = asyncio.Semaphore(concurrency)
        self.model = CLAUDE_MODEL

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((Exception,)),
        reraise=True
    )
    async def _generate_with_retry(self, prompt: str) -> str:
        """
        Generate content with retry logic.

        Args:
            prompt: The prompt to send to the model.

        Returns:
            The model's response text.
        """
        # Run sync API in executor to make it async
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
        )

        return response.content[0].text

    async def generate(self, prompt: str) -> str:
        """
        Generate content with rate limiting.

        Args:
            prompt: The prompt to send to the model.

        Returns:
            The model's response text.
        """
        async with self.semaphore:
            return await self._generate_with_retry(prompt)

    def _is_valid_score(self, score: float) -> bool:
        """Check if score is valid (0-6, whole or half points only)."""
        if not 0 <= score <= 6:
            return False
        # Check it's a whole or half point (0, 0.5, 1, 1.5, ..., 6)
        return score * 2 == int(score * 2)

    async def generate_boundary_scores(self, prompt: str) -> list[float] | None:
        """
        Generate boundary scores and parse the JSON response.

        Args:
            prompt: The prompt requesting boundary scores.

        Returns:
            List of float scores (0-6, half-points allowed), or None if parsing fails.
        """
        try:
            response = await self.generate(prompt)

            # Try to extract JSON array from response
            scores = self._parse_json_array(response)

            # Validate scores are in range and valid half-points
            if scores is not None:
                if all(self._is_valid_score(s) for s in scores):
                    return scores
                else:
                    safe_print(f"Warning: Claude scores out of range or invalid")
                    return None

            return None

        except Exception as e:
            safe_print(f"Error generating Claude boundary scores: {e}")
            return None

    def _parse_json_array(self, text: str) -> list[float] | None:
        """
        Extract and parse a JSON array from response text.

        Args:
            text: The response text that should contain a JSON array.

        Returns:
            Parsed list of floats, or None if parsing fails.
        """
        text = text.strip()

        # Find JSON array in text
        start = text.find('[')
        end = text.rfind(']')

        if start == -1 or end == -1 or end <= start:
            return None

        try:
            array_text = text[start:end + 1]
            result = json.loads(array_text)

            if not isinstance(result, list):
                return None

            # Handle both simple arrays and object arrays
            scores = []
            for item in result:
                if isinstance(item, dict):
                    # Object format: {"i": 0, "s": 2.0, ...}
                    score = item.get('s', item.get('score'))
                    if score is not None:
                        scores.append(float(score))
                else:
                    # Simple number
                    scores.append(float(item))

            return scores if scores else None

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            safe_print(f"Warning: Claude failed to parse JSON array: {e}")
            return None
