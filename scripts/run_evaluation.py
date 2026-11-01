"""运行金融智能体评测并输出脱敏聚合报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.evals.runner import run_evaluation, write_report  # noqa: E402


DEFAULT_DATASET = ROOT / "ai" / "evals" / "datasets" / "financial-agent-eval.v1.jsonl"
DEFAULT_REPORT = ROOT / "ai" / "evals" / "reports" / "offline-baseline.v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="显式调用已配置的 DeepSeek；默认只运行离线确定性基线。",
    )
    args = parser.parse_args()
    report = run_evaluation(args.dataset, live_llm=args.live_llm)
    write_report(report, args.output)
    print(json.dumps({
        "output": str(args.output),
        "mode": report["evaluation_mode"],
        "cases": report["dataset"]["case_count"],
        "metrics": report["metrics"],
        "security_gate": report["security_gate"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
