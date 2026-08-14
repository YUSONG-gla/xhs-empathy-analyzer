"""
Batch score cleaned datasets with the HEART rubric.

Usage from heart/backend:
    python calibration/batch_score.py --csv <dataset.csv>

Features:
  - Supports both legacy columns (STORY_ID/story) and cleaned columns
    (sample_id/text).
  - Resumable scoring via a JSON checkpoint file.
  - Exports both raw JSON results and a merged CSV with per-dimension scores
    plus the weighted total score.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schema import ScoreRequest
from services.scorer import score_text

DEFAULT_JSON_OUTPUT = Path(__file__).parent / "data" / "llm_scores.json"
DIMENSION_KEYS = [
    "vividness_emotion",
    "vividness_setting",
    "vulnerability",
    "cognition",
    "tone",
    "volume",
    "resolution",
    "development",
    "emo_shift",
]


def resolve_columns(df: pd.DataFrame, id_column: str | None, text_column: str | None) -> tuple[str, str]:
    """Pick explicit columns if provided, otherwise auto-detect known layouts."""
    if id_column and text_column:
        if id_column not in df.columns:
            raise ValueError(f"Missing id column: {id_column}")
        if text_column not in df.columns:
            raise ValueError(f"Missing text column: {text_column}")
        return id_column, text_column

    candidates = [
        ("sample_id", "text"),
        ("STORY_ID", "story"),
    ]
    for candidate_id, candidate_text in candidates:
        if candidate_id in df.columns and candidate_text in df.columns:
            return candidate_id, candidate_text

    raise ValueError(
        "Could not auto-detect id/text columns. Use --id-column and --text-column."
    )


def load_stories(csv_path: Path, id_column: str | None, text_column: str | None) -> tuple[list[dict[str, str]], pd.DataFrame, str, str]:
    """Load a dataset and return unique id/text pairs for scoring."""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    resolved_id, resolved_text = resolve_columns(df, id_column, text_column)

    story_df = (
        df[[resolved_id, resolved_text]]
        .dropna(subset=[resolved_text])
        .assign(**{resolved_text: lambda frame: frame[resolved_text].astype(str).str.strip()})
    )
    story_df = story_df[story_df[resolved_text] != ""]
    story_df = story_df.drop_duplicates(subset=[resolved_id], keep="first")

    stories = [
        {"story_id": str(row[resolved_id]), "text": str(row[resolved_text])}
        for _, row in story_df.iterrows()
    ]
    return stories, df, resolved_id, resolved_text


def load_existing(output_path: Path) -> dict[str, dict]:
    """Load existing JSON results for resume support."""
    if output_path.exists():
        with output_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {str(item["story_id"]): item for item in data}
    return {}


def save_all(results: dict[str, dict], output_path: Path) -> None:
    """Persist the full checkpoint JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(results.values(), key=lambda item: str(item["story_id"]))
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(ordered, handle, ensure_ascii=False, indent=2)


def write_scored_csv(
    source_df: pd.DataFrame,
    id_column: str,
    output_csv_path: Path,
    results: dict[str, dict],
) -> None:
    """Merge score results back into the original dataset and export a CSV."""
    rows: list[dict[str, object]] = []
    for row in source_df.to_dict(orient="records"):
        key = str(row[id_column])
        result = results.get(key, {})
        llm_scores = result.get("llm_scores", {})
        enriched = dict(row)
        for dim in DIMENSION_KEYS:
            enriched[dim] = llm_scores.get(dim, "")
        enriched["total_score"] = result.get("total_score", "")
        enriched["evaluation"] = result.get("evaluation", "")
        enriched["model_used"] = result.get("model_used", "")
        rows.append(enriched)

    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_csv_path, index=False, encoding="utf-8-sig")


async def score_one(story: dict[str, str], semaphore: asyncio.Semaphore) -> dict | None:
    """Score one text with concurrency control."""
    async with semaphore:
        story_id = story["story_id"]
        text = story["text"][:10000]
        try:
            request = ScoreRequest(text=text if len(text) >= 10 else text + "..." * 5)
            response = await score_text(request)
            return {
                "story_id": story_id,
                "llm_scores": {dimension.key: dimension.score for dimension in response.dimensions},
                "total_score": response.total_score,
                "evaluation": response.evaluation,
                "model_used": response.model_used,
            }
        except Exception as exc:
            print(f"  [WARN] story_id={story_id} scoring failed: {exc}", flush=True)
            return None


async def main(
    csv_path: Path,
    concurrency: int,
    output_json: Path,
    output_csv: Path,
    id_column: str | None,
    text_column: str | None,
) -> None:
    stories, source_df, resolved_id, _ = load_stories(csv_path, id_column, text_column)
    print(f"dataset rows to score: {len(stories)}", flush=True)

    existing = load_existing(output_json)
    pending = [story for story in stories if story["story_id"] not in existing]
    print(f"completed: {len(existing)} | pending: {len(pending)}", flush=True)

    if not pending:
        print("No pending rows. Re-exporting scored CSV from checkpoint.", flush=True)
        write_scored_csv(source_df, resolved_id, output_csv, existing)
        return

    semaphore = asyncio.Semaphore(concurrency)
    results = dict(existing)
    tasks = [score_one(story, semaphore) for story in pending]

    total = len(tasks)
    done = 0
    started_at = time.time()

    for coro in asyncio.as_completed(tasks):
        result = await coro
        done += 1
        if result:
            results[str(result["story_id"])] = result
            if done % 10 == 0:
                save_all(results, output_json)

        elapsed = time.time() - started_at
        speed = done / elapsed if elapsed > 0 else 0
        eta_seconds = (total - done) / speed if speed > 0 else 0
        print(
            f"  [{done}/{total}] speed={speed:.2f} rows/s | eta={eta_seconds / 60:.1f} min",
            flush=True,
        )

    save_all(results, output_json)
    write_scored_csv(source_df, resolved_id, output_csv, results)
    success = sum(1 for item in results.values() if "llm_scores" in item)
    print(f"\nDone. success={success} | json={output_json} | csv={output_csv}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch HEART scoring for CSV datasets")
    parser.add_argument("--csv", required=True, type=Path, help="Input CSV path")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent requests")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_OUTPUT, help="Checkpoint JSON path")
    parser.add_argument("--output-csv", type=Path, help="Merged scored CSV path")
    parser.add_argument("--id-column", help="Optional explicit id column")
    parser.add_argument("--text-column", help="Optional explicit text column")
    args = parser.parse_args()

    output_csv = args.output_csv or args.csv.with_name(f"{args.csv.stem}_scored.csv")
    asyncio.run(
        main(
            csv_path=args.csv,
            concurrency=args.concurrency,
            output_json=args.output_json,
            output_csv=output_csv,
            id_column=args.id_column,
            text_column=args.text_column,
        )
    )
