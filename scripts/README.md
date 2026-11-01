# Scripts

本目录保存本地初始化、接口导出和比赛演示检查脚本。

```powershell
# 交互式初始化两个演示账号的 Argon2id 凭据
python -m scripts.init_demo_credentials

# 从当前 FastAPI 路由生成可导入 Apifox 的契约
python -m scripts.export_openapi

# 检查 API、MySQL、DeepSeek、前端和飞书 Worker
powershell -ExecutionPolicy Bypass -File .\scripts\demo_preflight.ps1 -Strict
```

脚本不得读取后输出 `.env` 密钥。OpenAPI 导出会强制使用内存仓库，不连接或恢复开发数据库状态。

