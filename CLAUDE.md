# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

BigBill（变有钱）是一个个人资产配置追踪工具，按《资产配置方案》(当前 **v3.4**，文件 `资产配置方案_v3_4.md`) 管理五层投资组合，提供持仓截图 OCR 识别、再平衡计算、收益追踪和 AI 投资顾问。

**系统定位：辅助用户落地该方案** —— 方案是唯一标准，代码里的层级/阈值/规则都应与最新方案一致。改动前先核对 `资产配置方案_v3_4.md`（方案有版本演进，曾从 4 月旧版升级到 v3.4，层级定义与多项阈值随之变化，不要照搬旧版数字）。

v3.4 五层（按 order）：T1 安全垫 15-20% / T2 固收增强 15-20% / T3 权益核心 30-35% / T4 另类对冲(黄金) 8-12% / T5 全球分散(港股通/QDII) 10-15%。当前 DB 落点 20/20/35/12/13=100%。关键规则：单只个股 ≤ 总资产 **3%**；精选个股层单一申万一级行业 ≤ 该层 **50%**；层级偏离 >5pp 触发再平衡。这些是方案业务规则，不是可随意改的常量。

应用为中文（zh-hans），使用 `Asia/Shanghai` 时区，单用户。

**两个独立客户端共享同一套模型与逻辑设计**（彼此无共享代码、无网络通信）：
- **Django Web 应用**（仓库根目录）— 主实现，功能完整，SQLite + 服务端渲染。
- **iOS 应用**（`BigBillApp/`）— 独立的 SwiftUI + SwiftData 重新实现，纯本地存储，**不**与 Django 后端通信。在一端改业务逻辑时，通常需要手动在另一端做对应修改。

## 常用命令

### Django（主实现）
```bash
uv sync                                          # 安装依赖
uv run python manage.py migrate                  # 应用数据库迁移
uv run python manage.py shell < assets/seed_data.py   # 导入默认五层配置
uv run python manage.py runserver                # 开发服务器 http://127.0.0.1:8000/
uv run python manage.py makemigrations           # 修改模型后生成迁移

# 测试
uv run python manage.py test assets                                   # 全部
uv run python manage.py test assets.tests.RebalanceCalculationTest    # 单个测试类
uv run python manage.py test assets.tests.ModifiedDietzTest.test_with_transfer   # 单个测试方法
```
未配置 linter / formatter。测试集中在单文件 `assets/tests.py`。

### iOS 应用（`BigBillApp/`）
Xcode 工程由 `project.yml` 通过 [XcodeGen] 生成。仓库已提交生成好的 `.xcodeproj`，可直接打开；改动源码或 `project.yml` 后，在 `BigBillApp/` 目录内执行 `xcodegen generate` 重新生成。目标 iOS 17、Swift 6，在 Xcode 中构建运行（无 CI）。

## 环境与配置

应用运行期配置（LLM 服务商、API Key、模型名、已忽略的预警）存储在**数据库的 `Setting` 键值模型**中，通过 `/settings/` 页面编辑——不在环境变量或文件里。OCR 与 AI 顾问共用同一套服务商配置。

Django 环境变量（均可选，开发默认值合理）：`DJANGO_SECRET_KEY`、`DJANGO_DEBUG`（默认 true）、`DJANGO_ALLOWED_HOSTS`、`AUTH_REQUIRED`（默认 false）。

**鉴权**：当 `AUTH_REQUIRED=true` 时，所有页面/API 都被 `login_required` 包裹（经由 `assets/urls.py` 的 `_protected()`），需创建超级用户并通过 `/admin/login/` 登录；为 false（默认）时应用完全开放。新增路由请同样用 `_protected()` 包裹，以保持该开关有效。

## 架构（Django 应用）

单一 Django 应用 `assets`——服务端渲染模板 + JSON API 端点，未使用 DRF。

### 模型（`assets/models.py`）
- **AssetLayer** — 五个层级及目标比例；`total_market_value` 汇总该层持仓。
- **Holding** — 某层级下的持仓。`save()` 自动计算 `market_value`/`profit_loss`/`profit_loss_pct`；若缺成本价，会从市值 + 盈亏**反推估算成本**（并打 warning，应优先录入真实成本）。`is_reserve` 标记"干火药"，不参与再平衡。`industry`（申万一级行业）、`sub_category`（层内子类别：宽基/红利/精选个股/黄金/港股通/QDII/主题，留空则按 `asset_type` 回退——用于消歧，如 dividend_stock 既可能是红利ETF也可能是红利个股）、`buy_thesis`（买入逻辑）。新增持仓字段时，记得同步 import/backup-restore 的字段映射（`api_import_data`/`api_backup_create`/`api_backup_restore` 是显式逐字段拷贝，漏写会在恢复时丢数据）；并同步 holdings 表单的 `openEditModal` 位置参数。
- **Snapshot** — 时间点资产快照（`layer_values`/`layer_ratios`/`holdings_data` 为 JSON）。
- **Transaction** — 买入/卖出/分红/再平衡/转入/转出。`transfer`/`withdraw` 是 Modified Dietz 所需的现金流；`source` ∈ manual/auto/ocr；卖出时写 `realized_pnl`。`clean()` 拒绝未来日期。
- **Upload** — 截图 OCR 工作流：pending → processing → recognized → confirmed（或 failed）。
- **ChecklistRecord** — 周/月/季/年检视清单的完成记录。
- **EvaluationReport** / **AssetEvaluation** — 保存的 AI 顾问输出（整组合报告 vs 单资产深度评估，含 买入/卖出/持有/观望 信号）。
- **AlertAction** — 已处置的风控预警（如 `concentration_cap` 单只>3%、`bond_anomaly_*`、`drawdown_*`），带冷却期。
- **SystemBackup** — 全量状态 JSON 备份，用于恢复。
- **Setting** — 见上文配置；使用 `Setting.get(key, default)` / `Setting.set(key, value)`。

所有金额用 `Decimal`，以 `ROUND_HALF_UP` 量化。日期逻辑用 `timezone.localdate()`（不要用 `timezone.now().date()`）——UTC 与上海时区不同，会导致差一天的日期 bug。

### 服务（`assets/services/`）
- **ocr.py** — 持仓识别，支持 Anthropic SDK（`provider='anthropic'`，默认模型 `claude-sonnet-4-20250514`）或任意 OpenAI 兼容端点（httpx，`provider='openai_compatible'`，默认 `gpt-4o`；Ollama/vLLM/LM Studio）。图片压缩至 ≤800px；含截断 JSON 修复。
- **advisor.py** — AI 投资顾问，复用 OCR 的 `Setting` LLM 配置。prompt 内嵌 v3.4 规则（单只 ≤3%、单行业 ≤个股层50%、卖出五大信号、个股5项买入门槛、偏离 >5pp 再平衡、QDII 全球真分散）。
- **rebalance.py** — 偏差预警（>3pp 提醒，>5pp 警告）、新资金分配（优先填补最大缺口）、`calculate_risk_alerts` 逐笔风控，以及 v3.4 参考数据：`DRAWDOWN_PROTOCOLS`(§六 极端情景5类)、`DCA_RULES`(§5.2 分级DCA)、`GOLD_DUAL_INDICATOR`(§2.3 黄金双指标)、`STOCK_BUY_GATES`(§2.2.2 个股买入门槛)。
- **performance.py** — 基于快照 + 现金流交易的 Modified Dietz 区间收益率。
- **ledger.py** — 持仓变动时自动生成买入/卖出 Transaction，卖出时计算已实现盈亏。
- **cashflow.py** — 从快照差额 + 交易推断净注资/提现；检测初始投资缺口；构建月度图表。
- **integrity.py** — `run_integrity_checks()` 跨表对账（持仓合计 vs 层级合计 vs 最新快照；交易净额 vs 持仓数量），返回 pass/warning/fail。
- **allocation.py** — 层内子配置（v3.4：T3 宽基50-60/红利25-35/精选个股10-20 占本层；T4 黄金 8-12% 占总资产；T5 港股通30-40/QDII50-60/主题≤10 占本层）与行业集中度（单一申万一级 ≤ 精选个股层 50%）。分类经 `classify_subkey(h)`：优先 `Holding.sub_category`，留空时按 `asset_type` 回退；`is_pick(h)` 判定是否计入个股50%红线。`QDII_INTERNAL` 为 QDII 内部细分参考。供再平衡页与检视页使用。

风控口径（方案保真）：`calculate_risk_alerts` 接收可选 `market_history`（由 `build_market_history(snapshots, holdings)` 构建）。提供后，债基异常按「近一月」、第三层回撤按「距历史峰值」判断；不提供则回退到累计成本盈亏。**层级峰值按 `order` 聚合**（快照 holdings_data 存 `layer_order`；旧快照缺该字段时用当前持仓 id→order 回退）——这样层级改名/换层都不影响峰值查找。注意 v3.4 第五层已是「全球分散」纯DCA层，**不再有卫星式止盈止损阶梯**；精选个股退出靠「卖出五大信号 + 决策日志」。五层 `target_ratio` 之和必须 ≈100%（前端 + `api_settings_save` 后端双重校验）。

### 视图与 API 约定（`assets/views.py`、`assets/api_utils.py`）
页面视图渲染模板；`api_*` 视图用 `json.loads(request.body)` + `JsonResponse` 收发 JSON，`Decimal` 通过自定义 `DecimalEncoder` 序列化。

新增 API 端点应使用 `api_utils.py` 的 **`@api_endpoint` 装饰器**：抛出 `ApiError(code, message, status)`（或工厂方法 `invalid_input`/`not_found`/`conflict`/`internal_error`），它会返回结构化的 `{success, code, message}` 响应，并记录未预期异常而不泄露 traceback。

### 路由与前端
所有路由在 `assets/urls.py`，挂载于 `/` 下的 `assets` 命名空间。页面：`/`、`/holdings/`、`/upload/`、`/rebalance/`、`/cashflow/`、`/history/`、`/checklist/`、`/decisions/`（决策日志）、`/settings/`、`/assets/`、`/assets/<id>/`、`/advisor/`。API 在 `/api/...`。

模板位于 `assets/templates/assets/`，共享 `base.html`。JS 大多内联在各模板中，另有共享的 `assets/static/assets/js/app.js` 和 `css/style.css`；无构建步骤。弹窗通过切换 `.modal-overlay.active` CSS 类（而非 `style.display`）实现，以保留过渡动画。

## 跨会话续作

本项目常因用量上限在任务中途中断、跨多次会话开发。当用户说"继续"时，把未提交改动当作上次会话的进行中工作：查看 `git status`/`git diff`、`git log --oneline -5`，留意 `assets/` 下的未跟踪文件（新服务/模板/迁移），并运行测试看还有什么未完成。

[XcodeGen]: https://github.com/yonaskolb/XcodeGen
