"""Clean raw scraped texts and build one ML-ready category dataset.

This script merges multiple raw CSV files, normalizes text, filters out rows
outside the configured SCRAPE_PLAN categories, removes likely ad content, and
deduplicates by normalized content. The output is a single unified CSV that is
ready for downstream machine learning workflows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from calibration.scraper.config import AD_KEYWORDS, MAX_TEXT_LENGTH, MIN_TEXT_LENGTH, SCRAPE_PLAN


URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@[\w\u4e00-\u9fff.-]+")
TOPIC_RE = re.compile(r"#([^#\s]+)(?:\[[^\]]+\])?#")
SITE_SUFFIX_RE = re.compile(r"\s*[-|｜]\s*小红书\s*$")
SPACE_RE = re.compile(r"\s+")
EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002300-\U000023FF"
    "]+"
)


def clean_text(value: object, *, remove_social_tokens: bool = False) -> str:
    """Normalize Unicode, whitespace, URLs, and optional social tokens."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u200b", " ").replace("\ufeff", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = SITE_SUFFIX_RE.sub("", text)
    text = URL_RE.sub(" ", text)
    text = EMOJI_RE.sub(" ", text)
    if remove_social_tokens:
        text = MENTION_RE.sub(" ", text)
        text = TOPIC_RE.sub(r" \1 ", text)
        text = text.replace("#", " ")
    return SPACE_RE.sub(" ", text).strip()


def load_rows(input_paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for input_path in input_paths:
        with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row["source_file"] = input_path.name
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def build_category_ids() -> dict[str, int]:
    category_order: list[str] = []
    for task in SCRAPE_PLAN:
        if task.category not in category_order:
            category_order.append(task.category)
    return {category: index for index, category in enumerate(category_order)}


def prepare(input_paths: list[Path], output_path: Path) -> dict[str, object]:
    allowed_categories = set(build_category_ids())
    category_ids = build_category_ids()
    allowed_keywords = {task.keyword for task in SCRAPE_PLAN}

    raw_rows = load_rows(input_paths)
    clean_rows: list[dict[str, object]] = []
    seen_content: set[str] = set()
    excluded_reasons = Counter()
    label_counts = Counter()

    for raw in raw_rows:
        source_category = clean_text(raw.get("category", ""))
        source_keyword = clean_text(raw.get("keyword", ""))
        if source_category not in allowed_categories:
            excluded_reasons["category_not_in_scrape_plan"] += 1
            continue

        title = clean_text(raw.get("title", ""), remove_social_tokens=True)
        content = clean_text(raw.get("content", ""), remove_social_tokens=True)
        if len(content) < MIN_TEXT_LENGTH:
            excluded_reasons["content_too_short"] += 1
            continue
        if len(content) > MAX_TEXT_LENGTH:
            excluded_reasons["content_too_long"] += 1
            continue
        if any(ad_keyword in f"{title} {content}" for ad_keyword in AD_KEYWORDS):
            excluded_reasons["ad_keyword"] += 1
            continue

        normalized_content = content.casefold()
        if not normalized_content:
            excluded_reasons["empty_content"] += 1
            continue
        if normalized_content in seen_content:
            excluded_reasons["duplicate_content"] += 1
            continue
        seen_content.add(normalized_content)

        text = content if not title or title in content else f"{title}\n\n{content}"
        sample_id = stable_hash(f"{source_category}|{normalized_content}")
        like_count_raw = clean_text(raw.get("like_count", ""))
        try:
            like_count: object = int(float(like_count_raw)) if like_count_raw else ""
        except ValueError:
            like_count = ""

        clean_rows.append(
            {
                "sample_id": sample_id,
                "text": text,
                "title": title,
                "content": content,
                "text_length": len(text),
                "label": source_category,
                "label_id": category_ids[source_category],
                "source_category": source_category,
                "source_keyword": source_keyword,
                "keyword_in_scrape_plan": int(source_keyword in allowed_keywords),
                "source_post_id": clean_text(raw.get("post_id", "")),
                "like_count": like_count,
                "source_file": clean_text(raw.get("source_file", "")),
            }
        )
        label_counts[source_category] += 1

    clean_rows.sort(key=lambda row: (int(row["label_id"]), -int(row["like_count"] or 0), str(row["sample_id"])))
    fieldnames = [
        "sample_id",
        "text",
        "title",
        "content",
        "text_length",
        "label",
        "label_id",
        "source_category",
        "source_keyword",
        "keyword_in_scrape_plan",
        "source_post_id",
        "like_count",
        "source_file",
    ]
    write_csv(output_path, clean_rows, fieldnames)

    report = {
        "input_files": [str(path) for path in input_paths],
        "output_file": str(output_path),
        "raw_rows": len(raw_rows),
        "clean_rows": len(clean_rows),
        "removed_rows": len(raw_rows) - len(clean_rows),
        "allowed_categories": list(category_ids.keys()),
        "label_counts": dict(label_counts),
        "excluded_reasons": dict(excluded_reasons),
        "notes": [
            "Rows are labeled by SCRAPE_PLAN category for category-level classification.",
            "Rows outside SCRAPE_PLAN categories are excluded.",
            "keyword_in_scrape_plan marks whether the original keyword was explicitly listed in SCRAPE_PLAN.",
            "Duplicates are removed using normalized content only.",
        ],
    }
    report_path = output_path.with_suffix(".report.json")
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


def main() -> None:
    base_dir = Path(__file__).parent / "data"
    default_inputs = [
        base_dir / "raw_real_texts.csv",
        base_dir / "raw_real_texts2.csv",
        base_dir / "raw_real_texts3.csv",
    ]

    parser = argparse.ArgumentParser(description="Prepare one ML-ready category dataset from raw scraped CSV files")
    parser.add_argument("--inputs", type=Path, nargs="*", default=default_inputs)
    parser.add_argument("--output", type=Path, default=base_dir / "processed" / "ml_category_dataset.csv")
    args = parser.parse_args()

    report = prepare(args.inputs, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
