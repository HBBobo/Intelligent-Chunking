"""
Async Gemini API client with rate limiting and retry logic.
"""

import asyncio
import json
import sys
import google.generativeai as genai
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from .config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
    MAX_RETRIES
)


def safe_print(msg: str = "") -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode())


class GeminiClient:
    """Async wrapper for Gemini API with rate limiting."""

    def __init__(self, concurrency: int = 5):
        """
        Initialize the Gemini client.

        Args:
            concurrency: Maximum number of concurrent API calls.
        """
        if not GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY not found in environment. "
                "Please set it in your .env file."
            )

        genai.configure(api_key=GEMINI_API_KEY)

        self.model = genai.GenerativeModel(GEMINI_MODEL)
        self.semaphore = asyncio.Semaphore(concurrency)
        self.temperature = GEMINI_TEMPERATURE

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
            lambda: self.model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=self.temperature,
                )
            )
        )

        return response.text

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
                    safe_print(f"Warning: Scores out of range or invalid: {scores}")
                    return None

            return None

        except Exception as e:
            safe_print(f"Error generating boundary scores: {e}")
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

        # Try direct parsing first
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return [float(x) for x in result]
        except (json.JSONDecodeError, ValueError):
            pass

        # Try to find JSON array in text
        start = text.find('[')
        end = text.rfind(']')

        if start != -1 and end != -1 and end > start:
            try:
                array_text = text[start:end + 1]
                result = json.loads(array_text)
                if isinstance(result, list):
                    return [float(x) for x in result]
            except (json.JSONDecodeError, ValueError):
                pass

        return None
