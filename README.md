# BigMoney 大钱 💰

**A personal asset allocation tracker built on financial advice provided by Claude (Anthropic).**

The investment methodology — a five-layer portfolio structure (Safety Cushion / Bond Core / Equity Core / Alternative Hedge / Satellite Opportunities) — was designed with guidance from Claude AI. The application itself was also largely built with Claude Code.

> **Disclaimer:** This tool is for personal tracking purposes only. It is NOT professional financial advice. The portfolio structure and rebalancing logic reflect AI-assisted suggestions and should be evaluated by a qualified financial advisor before use. Use at your own risk.

## Features

- **Five-Layer Portfolio Model** — structured asset allocation with target ratios and automatic deviation alerts
- **Screenshot OCR** — upload brokerage/bank screenshots; AI extracts holdings automatically (supports Anthropic Claude API and OpenAI-compatible endpoints like Ollama)
- **Rebalancing Engine** — deviation warnings (>3% caution, >5% critical), new fund allocation suggestions, and drawdown response protocols
- **Performance Tracking** — Modified Dietz method for time-weighted interval return calculation
- **Snapshot History** — point-in-time portfolio snapshots with layer composition charts
- **Transaction Log** — buy/sell/transfer/withdraw records for cash flow tracking

## Quick Start

```bash
# Install dependencies (requires uv)
uv sync

# Initialize database
uv run python manage.py migrate

# Seed the default 5-layer asset configuration
uv run python manage.py shell < assets/seed_data.py

# Run the server
uv run python manage.py runserver
```

Then visit http://127.0.0.1:8000/ and configure your LLM provider in Settings.

## Tech Stack

- **Backend:** Django 6 / Python
- **Database:** SQLite (single-user, no auth)
- **Frontend:** Server-rendered Django templates, inline JS, no build step
- **OCR:** Anthropic Claude API or any OpenAI-compatible endpoint

## AI Attribution

This project is built on financial portfolio advice provided by [Claude](https://claude.ai) (Anthropic). The five-layer investment framework, rebalancing thresholds, and drawdown response protocols were all designed through conversations with Claude. The codebase was developed with [Claude Code](https://claude.ai/code).

---

# BigMoney 大钱 💰

**基于 Claude (Anthropic) 提供的理财建议构建的个人资产配置追踪工具。**

投资方法论——五层资产配置体系（安全垫 / 债券核心 / 股票核心 / 另类对冲 / 卫星机会）——由 Claude AI 指导设计。应用本身也主要由 Claude Code 开发完成。

> **免责声明：** 本工具仅供个人记录用途，不构成专业投资建议。资产配置体系与再平衡逻辑来源于 AI 辅助建议，使用前请咨询专业理财顾问。风险自负。

## 功能特性

- **五层资产配置模型** — 结构化资产分层，设定目标比例并自动偏差预警
- **截图 OCR 识别** — 上传券商/银行持仓截图，AI 自动提取持仓信息（支持 Anthropic Claude API 及 Ollama 等 OpenAI 兼容接口）
- **再平衡引擎** — 偏差预警（>3% 提醒，>5% 警告）、新资金分配建议、回撤应对方案
- **收益追踪** — 基于 Modified Dietz 方法的时间加权区间收益率计算
- **快照历史** — 时间点资产快照，含层级构成演变图表
- **交易日志** — 买入/卖出/注资/提现记录，用于现金流追踪

## 快速开始

```bash
# 安装依赖（需要 uv）
uv sync

# 初始化数据库
uv run python manage.py migrate

# 导入默认五层资产配置
uv run python manage.py shell < assets/seed_data.py

# 启动服务
uv run python manage.py runserver
```

访问 http://127.0.0.1:8000/，在设置页配置 LLM 服务商即可使用。

## 技术栈

- **后端：** Django 6 / Python
- **数据库：** SQLite（单用户，无需认证）
- **前端：** Django 模板渲染，内联 JS，无构建步骤
- **OCR：** Anthropic Claude API 或任意 OpenAI 兼容接口

## AI 声明

本项目基于 [Claude](https://claude.ai) (Anthropic) 提供的理财投资建议构建。五层资产配置框架、再平衡阈值、回撤应对策略均通过与 Claude 的对话设计而成。代码由 [Claude Code](https://claude.ai/code) 开发。
