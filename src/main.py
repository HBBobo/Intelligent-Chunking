"""
CLI entry point for training data generation.
"""

import argparse
import asyncio
import sys
from pathlib import Path


def safe_print(msg: str = "") -> None:
    """Print safely, handling encoding errors on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode())


from .config import (
    DEFAULT_WINDOW_SIZE,
    DEFAULT_OVERLAP,
    DEFAULT_CONCURRENCY,
    DATA_RAW_DIR,
    DATA_PROCESSED_DIR
)
from .pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate training data for semantic boundary scoring",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=DATA_RAW_DIR,
        help="Input file or directory containing text files (.txt, .md)"
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=DATA_PROCESSED_DIR / "training_data.jsonl",
        help="Output JSONL file path"
    )

    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Maximum concurrent API calls"
    )

    parser.add_argument(
        "--window-size", "-w",
        type=int,
        default=DEFAULT_WINDOW_SIZE,
        help="Number of sentences per window"
    )

    parser.add_argument(
        "--overlap",
        type=int,
        default=DEFAULT_OVERLAP,
        help="Number of overlapping sentences between windows"
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Validate input
    if not args.input.exists():
        safe_print(f"Error: Input path does not exist: {args.input}")
        return 1

    # Validate parameters
    if args.overlap >= args.window_size:
        safe_print("Error: Overlap must be less than window size")
        return 1

    safe_print(f"Input: {args.input}")
    safe_print(f"Output: {args.output}")
    safe_print(f"Window size: {args.window_size}")
    safe_print(f"Overlap: {args.overlap}")
    safe_print(f"Concurrency: {args.concurrency}")
    safe_print()

    # Run pipeline
    try:
        count = asyncio.run(run_pipeline(
            input_path=args.input,
            output_path=args.output,
            window_size=args.window_size,
            overlap=args.overlap,
            concurrency=args.concurrency
        ))

        if count > 0:
            safe_print(f"\nSuccess! Generated {count} boundary labels.")
            return 0
        else:
            safe_print("\nNo boundaries generated. Check input files.")
            return 1

    except KeyboardInterrupt:
        safe_print("\nInterrupted by user")
        return 130

    except Exception as e:
        import traceback
        safe_print(f"\nError: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
