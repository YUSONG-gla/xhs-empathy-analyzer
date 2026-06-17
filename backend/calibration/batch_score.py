"""
阶段 1：对 874 条故事批量 LLM 评分（一次性脚本）

用法（在 backend/ 目录下运行）:
    python calibration/batch_score.py --csv PATH_TO_DATASET_FINAL_backup.csv

输出:
    calibration/data/llm_scores.json

特性:
  - 断点续跑：已评分的 story_id 自动跳过，不重复消耗 API
  - 异步并发：默认 3 并发（可通过 --concurrency 调整，避免超出速率限制）
  - 进度打印：每条故事完成后实时输出
  - 错误跳过：单条失败后记录错误继续运行
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import pandas as pd

# 确保 backend/ 在 sys.path 中（从 backend/ 目录运行时已自动满足）
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schema import ScoreRequest
from services.scorer import score_text

OUTPUT_PATH = Path(__file__).parent / "data" / "llm_scores.json"


def load_stories(csv_path: str) -> list[dict]:
    """加载 CSV，按 STORY_ID 聚合，返回 874 条故事列表"""
    df = pd.read_csv(csv_path, encoding="utf-8")

    # 每个 STORY_ID 取第一行的 story 文本（同一故事文本相同）
    story_df = df.groupby("STORY_ID", as_index=False).agg({"story": "first"})
    stories = [
        {"story_id": str(row["STORY_ID"]), "text": str(row["story"])}
        for _, row in story_df.iterrows()
        if pd.notna(row["story"]) and str(row["story"]).strip()
    ]
    return stories


def load_existing(output_path: Path) -> dict[str, dict]:
    """加载已有结果（断点续跑）"""
    if output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)
        return {item["story_id"]: item for item in data}
    return {}


def save_all(results: dict[str, dict], output_path: Path):
    """将全部结果写入 JSON"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(list(results.values()), f, ensure_ascii=False, indent=2)


async def score_one(story: dict, semaphore: asyncio.Semaphore) -> dict | None:
    """评分单条故事，限速通过 semaphore"""
    async with semaphore:
        story_id = story["story_id"]
        text = story["text"][:10000]   # 截断超长文本
        try:
            req = ScoreRequest(text=text if len(text) >= 10 else text + "..." * 5)
            resp = await score_text(req)
            return {
                "story_id": story_id,
                "llm_scores": {d.key: d.score for d in resp.dimensions},
            }
        except Exception as e:
            print(f"  [WARN] story_id={story_id} 评分失败: {e}", flush=True)
            return None


async def main(csv_path: str, concurrency: int):
    stories = load_stories(csv_path)
    print(f"数据集共 {len(stories)} 条故事", flush=True)

    existing = load_existing(OUTPUT_PATH)
    pending = [s for s in stories if s["story_id"] not in existing]
    print(f"已完成: {len(existing)} | 待评分: {len(pending)}", flush=True)

    if not pending:
        print("全部已评分，无需重新运行。")
        return

    semaphore = asyncio.Semaphore(concurrency)
    results = dict(existing)   # 复制已有结果

    tasks = [score_one(s, semaphore) for s in pending]
    total = len(tasks)
    done = 0
    t0 = time.time()

    for coro in asyncio.as_completed(tasks):
        result = await coro
        done += 1
        if result:
            results[result["story_id"]] = result
            # 每完成 10 条保存一次（断点保护）
            if done % 10 == 0:
                save_all(results, OUTPUT_PATH)

        elapsed = time.time() - t0
        speed = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / speed if speed > 0 else 0
        print(
            f"  [{done}/{total}] 速度={speed:.1f}条/s | 预计剩余={eta/60:.1f}分钟",
            flush=True,
        )

    # 最终保存
    save_all(results, OUTPUT_PATH)
    success = sum(1 for r in results.values() if "llm_scores" in r)
    print(f"\n完成！成功={success} | 输出路径={OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HEART 批量 LLM 评分")
    parser.add_argument("--csv", required=True, help="DATASET_FINAL_backup.csv 路径")
    parser.add_argument("--concurrency", type=int, default=3, help="并发请求数（默认3）")
    args = parser.parse_args()

    asyncio.run(main(args.csv, args.concurrency))
