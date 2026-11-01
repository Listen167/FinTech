# AI 评测与红队基线

本目录保存可重复运行的中文金融智能体评测资产：

- `datasets/financial-agent-eval.v1.jsonl`：380 条普通意图、模糊表达、多轮补槽和攻击样本；
- `reports/offline-baseline.v1.json`：不调用外部模型的确定性基线；
- `runner.py`：统一统计准确率、槽位完整率、回退率、误执行率、越权成功率和 P95 延迟；
- `test_evaluation_framework.py`：校验数据规模、版本关联、脱敏约束和红队零成功门禁。

重新生成并运行：

```powershell
python scripts/generate_eval_dataset.py
python scripts/run_evaluation.py
```

只有显式传入 `--live-llm` 才会调用 `.env` 中配置的 DeepSeek：

```powershell
python scripts/run_evaluation.py --live-llm --output ai/evals/reports/deepseek-live.local.json
```

在线报告和离线报告不得混用。离线模式的模型回退率为 100%，仅证明确定性回退链路，不代表 DeepSeek 在线模型成绩。报告不保存原始消息、用户标识、账号、Token 或第三方渠道 ID。

