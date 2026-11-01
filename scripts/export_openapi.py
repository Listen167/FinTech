"""从当前 FastAPI 路由生成可导入 Apifox 的 OpenAPI JSON。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "AI_Banking_Agent_OpenAPI.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="导出当前 FastAPI OpenAPI 契约")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    # OpenAPI 生成不应连接或恢复开发数据库中的业务状态。
    os.environ["DATABASE_ENABLED"] = "false"
    from backend.app.main import app

    document = app.openapi()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"已导出 OpenAPI {document['openapi']} / API {document['info']['version']}，"
        f"共 {len(document['paths'])} 条路径：{output}"
    )


if __name__ == "__main__":
    main()
