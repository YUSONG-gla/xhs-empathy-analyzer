"""
阶段 3：校准效果评估（可重复运行）

用法（在 backend/ 目录下运行）:
    python calibration/evaluator.py --csv PATH_TO_DATASET_FINAL_backup.csv

输出（stdout）:
    各维度校准前/后 Pearson r、MAE、Δr 对比表
    以及各维度的 Weighted F1（离散化后分类准确性）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import numpy as np
import joblib
from scipy.stats import pearsonr
from sklearn.metrics import f1_score

# 复用 trainer 中的数据加载逻辑
from calibration.trainer import (
    load_human_labels, normalize_human, load_llm_scores,
    build_matrices, DIMENSIONS,
)
from models.schema import VALID_SCORES

LLM_SCORES_PATH = Path(__file__).parent / "data"      / "llm_scores.json"
ARTIFACTS_PATH  = Path(__file__).parent / "artifacts" / "calibrators.pkl"


def snap_to_legal(cal_normalized: np.ndarray, dim: str) -> np.ndarray:
    """将归一化预测值映射回最近合法 HEART 分值，再归一化回 [0,1]"""
    legal = VALID_SCORES.get(dim, [2, 4, 6, 8, 10])
    raw = cal_normalized * 8.0 + 2.0
    snapped = np.array([min(legal, key=lambda v: abs(v - r)) for r in raw])
    return (snapped - 2.0) / 8.0   # 归一化用于与 y（[0,1]）对比


def evaluate(csv_path: str):
    # 加载
    story_df = normalize_human(load_human_labels(csv_path))
    llm_scores = load_llm_scores(LLM_SCORES_PATH)
    X, y, _ = build_matrices(llm_scores, story_df)

    if not ARTIFACTS_PATH.exists():
        print(f"错误: 校准器不存在 ({ARTIFACTS_PATH})\n请先运行 trainer.py")
        sys.exit(1)

    calibrators = joblib.load(ARTIFACTS_PATH)

    header = f"{'维度':<30} {'校准前 r':>9} {'校准后 r':>9} {'Δr':>7} {'MAE':>7} {'F1↑':>7}"
    print("=" * len(header))
    print("HEART 校准效果评估报告（全量数据）")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    delta_rs, maes = [], []

    for i, dim in enumerate(DIMENSIONS):
        x_col = X[:, i]
        y_col = y[:, i]

        # 校准前
        r_before = pearsonr(x_col, y_col).statistic

        # 校准后（含 snap 到合法值）
        cal_raw = np.array([float(calibrators[dim].predict([x])[0]) for x in x_col])
        cal_snapped = snap_to_legal(cal_raw, dim)
        r_after = pearsonr(cal_snapped, y_col).statistic
        mae = float(np.mean(np.abs(cal_snapped - y_col)))

        # Weighted F1（离散化到合法值集合后的分类 F1）
        legal = sorted(VALID_SCORES.get(dim, [2, 4, 6, 8, 10]))
        y_raw = y_col * 8.0 + 2.0
        y_cls = np.array([min(legal, key=lambda v: abs(v - r)) for r in y_raw])
        x_raw = x_col * 8.0 + 2.0
        x_cls = np.array([min(legal, key=lambda v: abs(v - r)) for r in x_raw])
        cal_cls_raw = cal_raw * 8.0 + 2.0
        cal_cls = np.array([min(legal, key=lambda v: abs(v - r)) for r in cal_cls_raw])

        f1_after = f1_score(y_cls, cal_cls, average="weighted", zero_division=0)

        delta_r = r_after - r_before
        delta_rs.append(delta_r)
        maes.append(mae)

        flag = "✓" if delta_r > 0.05 else ("~" if delta_r > 0 else "✗")
        print(
            f"{dim:<30} {r_before:>9.3f} {r_after:>9.3f} "
            f"{delta_r:>+7.3f} {mae:>7.3f} {f1_after:>7.3f}  {flag}"
        )

    print("-" * len(header))
    print(
        f"{'平均':<30} {'':>9} {'':>9} "
        f"{np.mean(delta_rs):>+7.3f} {np.mean(maes):>7.3f}"
    )
    print()
    print("✓=Δr>0.05（显著提升）  ~=Δr>0（轻微提升）  ✗=Δr≤0（未改善）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HEART 校准效果评估")
    parser.add_argument("--csv", required=True, help="DATASET_FINAL_backup.csv 路径")
    args = parser.parse_args()
    evaluate(args.csv)
