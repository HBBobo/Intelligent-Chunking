"""
Configuration and constants for the training data generation pipeline.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_TEMPERATURE = 0.0

# Window parameters
DEFAULT_WINDOW_SIZE = 100  # sentences per window
DEFAULT_OVERLAP = 20       # overlap sentences between windows
DEFAULT_CONTEXT_SIZE = 3   # sentences before/after for training context

# Concurrency settings
DEFAULT_CONCURRENCY = 5    # max parallel API calls
MAX_RETRIES = 3            # retry attempts for failed API calls

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPT_TEMPLATE_PATH = CONFIG_DIR / "prompt_template.txt"

# Ensure directories exist
DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
