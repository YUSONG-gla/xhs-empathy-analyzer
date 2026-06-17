"""
阶段 2：训练 ML 校准器（一次性脚本）

前置条件：
    calibration/data/llm_scores.json 已由 batch_score.py 生成

用法（在 backend/ 目录下运行）:
    python calibration/trainer.py --csv PATH_TO_DATASET_FINAL_backup.csv

输出:
    calibration/artifacts/calibrators.pkl

原理：
    每个维度独立训练一个 IsotonicRegression（保序回归）：
      X = LLM 打出的分数（归一化到 [0,1]）
      y = 人工标注均值（归一化到 [0,1]）
    保序回归假设：LLM 高分 → 人工高分（单调性），非参数，适合小样本
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import json
import numpy as np
import pandas as pd
import joblib
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split

DIMENSIONS = [
    "vividness_emotion", "vividness_setting", "vulnerability",
    "cognition", "tone", "volume", "resolution", "development", "emo_shift",
]

# 人工标注列及归一化范围
NORMALIZATION = {
    "vividness_emotion": {"col": "STATE_EMPATHY_Affective_scored",    "min": 1, "max": 5},
    "vividness_setting": {"col": "TRANSPORTATION_Imaginative_scored",  "min": 1, "max": 7},
    "vulnerability":     {"col": "VULNERABILITY_incompetence",         "min": 0, "max": 1},
    "cognition":         {"col": "STATE_EMPATHY_Cognitive_scored",     "min": 1, "max": 5},
    "tone":              {"col": "VALENCE_scored",                     "min": 1, "max": 10},
    "volume":            {"col": "volume",                             "min": 0, "max": 1},
    "resolution":        {"col": "GPT_resolution",                    "min": 0, "max": 1},
    "development":       {"col": "GPT_character_development",         "min": 0, "max": 1},
    "emo_shift":         {"col": "GPT_emotion_shifts",                "min": 0, "max": 1},
}

LLM_SCORES_PATH  = Path(__file__).parent / "data"      / "llm_scores.json"
ARTIFACTS_PATH   = Path(__file__).parent / "artifacts" / "calibrators.pkl"


def load_human_labels(csv_path: str) -> pd.DataFrame:
    """
    加载 CSV，按 STORY_ID 聚合，返回每个故事的人工标注均值（或故事级属性）。
    同一故事有多个评分者行，人工分取均值，故事级属性取 first。
    """
    df = pd.read_csv(csv_path, encoding="utf-8")

    # 将数值列转为 float，非法值变 NaN
    for cfg in NORMALIZATION.values():
        df[cfg["col"]] = pd.to_numeric(df[cfg["col"]], errors="coerce")

    agg_rules = {}
    # 人工评分维度取均值
    human_cols = ["STATE_EMPATHY_Affective_scored", "TRANSPORTATION_Imaginative_scored",
                  "STATE_EMPATHY_Cognitive_scored", "VALENCE_scored"]
    for col in human_cols:
        agg_rules[col] = "mean"

    # 故事级属性取 first（各评分者行相同）
    story_cols = ["VULNERABILITY_incompetence", "volume",
                  "GPT_resolution", "GPT_character_development", "GPT_emotion_shifts"]
    for col in story_cols:
        agg_rules[col] = "first"

    story_df = df.groupby("STORY_ID", as_index=True).agg(agg_rules)
    return story_df


def normalize_human(story_df: pd.DataFrame) -> pd.DataFrame:
    """将人工标注各维度归一化到 [0, 1]"""
    for dim, cfg in NORMALIZATION.items():
        col = cfg["col"]
        lo, hi = cfg["min"], cfg["max"]
        story_df[f"{dim}_y"] = ((story_df[col] - lo) / (hi - lo)).clip(0, 1)
    return story_df


def load_llm_scores(llm_path: Path) -> dict[str, dict[str, float]]:
    """
    加载 llm_scores.json，返回 {story_id: {dim: score}} 结构
    LLM 分在 [2, 10]，归一化到 [0, 1] 在训练时处理
    """
    with open(llm_path, encoding="utf-8") as f:
        records = json.load(f)
    return {str(r["story_id"]): r["llm_scores"] for r in records if "llm_scores" in r}


def build_matrices(
    llm_scores: dict[str, dict[str, float]],
    story_df: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    对齐 LLM 分与人工分，构建 X, y 矩阵。
    只保留两侧均有数据且无 NaN 的故事。

    Returns:
        X: shape (N, 9), LLM 分归一化到 [0,1]
        y: shape (N, 9), 人工分归一化到 [0,1]
        story_ids: 保留的 story_id 列表
    """
    X_rows, y_rows, ids = [], [], []

    for story_id, llm_dim_scores in llm_scores.items():
        sid = int(story_id) if story_id.isdigit() else story_id
        if sid not in story_df.index:
            continue

        row = story_df.loc[sid]

        # 检查该行是否有 NaN
        y_cols = [f"{dim}_y" for dim in DIMENSIONS]
        if row[y_cols].isna().any():
            continue

        # X: LLM 分 → [0, 1]
        x = np.array([
            (float(llm_dim_scores.get(dim, 2.0)) - 2.0) / 8.0
            for dim in DIMENSIONS
        ], dtype=np.float64).clip(0, 1)

        # y: 人工分（已归一化）
        y = np.array([float(row[f"{dim}_y"]) for dim in DIMENSIONS], dtype=np.float64)

        X_rows.append(x)
        y_rows.append(y)
        ids.append(story_id)

    return np.array(X_rows), np.array(y_rows), ids


def train(X: np.ndarray, y: np.ndarray) -> dict:
    """为每个维度独立训练 IsotonicRegression"""
    calibrators = {}
    for i, dim in enumerate(DIMENSIONS):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(X[:, i], y[:, i])
        calibrators[dim] = iso
        print(f"  训练 {dim}: {len(X)} 个样本 | y 均值={y[:, i].mean():.3f}")
    return calibrators


def quick_eval(calibrators: dict, X_test: np.ndarray, y_test: np.ndarray):
    """训练完成后在测试集上做快速评估"""
    from scipy.stats import pearsonr
    print("\n── 测试集快速评估（20% 留出）──")
    print(f"{'维度':<30} {'校准前 r':>10} {'校准后 r':>10} {'MAE':>8}")
    print("-" * 62)
    for i, dim in enumerate(DIMENSIONS):
        x_col = X_test[:, i]
        y_col = y_test[:, i]

        r_before = pearsonr(x_col, y_col).statistic if len(x_col) > 2 else float("nan")

        cal = np.array([
            float(calibrators[dim].predict([x])[0]) for x in x_col
        ])
        r_after = pearsonr(cal, y_col).statistic if len(cal) > 2 else float("nan")
        mae = float(np.mean(np.abs(cal - y_col)))

        print(f"{dim:<30} {r_before:>10.3f} {r_after:>10.3f} {mae:>8.3f}")


def main(csv_path: str):
    print("=== HEART 校准器训练 ===\n")

    # 1. 检查 LLM 评分文件
    if not LLM_SCORES_PATH.exists():
        print(f"错误: {LLM_SCORES_PATH} 不存在，请先运行 batch_score.py")
        sys.exit(1)

    # 2. 加载数据
    print("加载人工标注数据集...")
    story_df = load_human_labels(csv_path)
    story_df = normalize_human(story_df)
    print(f"  → 故事数: {len(story_df)}")

    print("加载 LLM 评分...")
    llm_scores = load_llm_scores(LLM_SCORES_PATH)
    print(f"  → LLM 评分条数: {len(llm_scores)}")

    # 3. 构建矩阵
    X, y, ids = build_matrices(llm_scores, story_df)
    print(f"\n有效对齐样本数: {len(ids)}")
    if len(ids) < 50:
        print(f"警告: 样本数偏少（{len(ids)}），校准效果可能不稳定")

    # 4. 训练/测试分割
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    print(f"训练集: {len(X_train)} | 测试集: {len(X_test)}\n")

    # 5. 训练
    print("训练保序回归校准器...")
    calibrators = train(X_train, y_train)

    # 6. 快速评估
    try:
        quick_eval(calibrators, X_test, y_test)
    except ImportError:
        print("（安装 scipy 可查看 Pearson r 指标：pip install scipy）")

    # 7. 保存
    ARTIFACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(calibrators, ARTIFACTS_PATH)
    print(f"\n✓ 校准器已保存至: {ARTIFACTS_PATH}")
    print("  下次启动 uvicorn 时将自动加载。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="训练 HEART ML 校准器")
    parser.add_argument("--csv", required=True, help="DATASET_FINAL_backup.csv 路径")
    args = parser.parse_args()
    main(args.csv)
