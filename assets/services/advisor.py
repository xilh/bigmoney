"""
AI 投资顾问服务
使用大模型对持仓进行综合评估，给出买入/卖出/持有建议及风险预警。
复用 Setting 中的 LLM 配置（与 OCR 共享 provider 设置）。
"""
import json
import logging
import time
from datetime import date

logger = logging.getLogger(__name__)

import anthropic
import httpx

from ..models import Setting


ADVISOR_PROMPT = """你是一位专业的个人投资顾问（CFA 持证），正在为客户审查其投资组合。
请基于以下持仓数据和配置信息，对每个持仓给出**简明**的评估意见。

## 评估维度

对每个持仓，请从以下角度分析：
1. **投资逻辑验证**：判断当前该资产（特别是股票）的投资逻辑是否破坏、估值是否极端或行业是否发生变局。这是主动卖出的核心信号。
2. **盈亏状态与停损止盈**：
   - 核心持仓大幅回撤时（如>-30%），是否具备护城河以度过周期。
   - 第五层卫星仓位必须严格止盈止损（-30%警示，-50%止损，-70%清仓；+50%/+100%/+200%阶梯止盈）。
3. **仓位集中度（红线）**：任何单只股票占总资产比例 **不得超过 5%**。若大于 5%，必须给出强烈的减仓卖出信号。
4. **资产类型适配**：该资产是否适合所在层级
5. **操作建议**：给出 买入(buy) / 卖出(sell) / 持有(hold) / 观望(watch) 之一，以及单只最高5%限流的约束。

## 整体组合评估

在逐个分析后，请给出整体组合的评估：
1. 资产配置是否均衡、是否触发再平衡点（>5%偏差需要年度/触发再平衡）
2. 风险集中度（行业/平台/资产类型，特别是单票>5%违规情况）
3. 值得庆祝的亮点（如有显著盈利的持仓）
4. 需要关注的风险点

## 输出格式

请返回纯 JSON（不要 markdown 代码块），格式如下：
{
  "holdings": [
    {
      "name": "持仓名称",
      "signal": "buy|sell|hold|watch",
      "signal_reason": "一句话理由",
      "risk_level": "low|medium|high|critical",
      "comment": "2-3句详细评价，包含具体的数值分析"
    }
  ],
  "portfolio_summary": {
    "overall_health": "healthy|caution|warning|critical",
    "score": 85,
    "highlights": ["亮点1", "亮点2"],
    "warnings": ["风险点1", "风险点2"],
    "advice": "一段整体建议（50-100字）"
  }
}

## 注意事项
- 本次审核需要极度关注 5% 单票上限机制，如果集中度>5%请严肃警告。
- 对于盈利超过 20% 的持仓，如果基本面没有变化可以在 comment 中加入庆祝性语言。
- 对于亏损超过 30%的任何持仓，请重点质问投资逻辑是否破产。
- score 是 0-100 的整数，代表组合健康度。
"""


ASSET_EVAL_PROMPT = """你是一位专业的个人投资顾问（CFA 持证），正在为客户**深度分析一只具体的资产**。
你将收到该资产的详细信息，以及客户的完整投资组合概况，请结合组合整体来分析。

## 投资原则（必须遵守）

客户采用五层资产配置体系（安全垫/债券核心/股票核心/另类对冲/卫星机会），每层有目标配比。
- **5% 单票上限**：任何单只股票占总资产比例不得超过 5%，超过必须强烈警告减仓。
- **核心持仓**（第1-3层）：大幅回撤时（>-30%），需判断是否具备护城河以度过周期。
- **卫星仓位**（第5层）：严格止盈止损（-30%警示，-50%止损，-70%清仓；+50%/+100%/+200%阶梯止盈）。
- **再平衡**：层级偏差 >5% 触发再平衡。
- 投资逻辑破坏是主动卖出的核心信号。

## 评估维度

1. **基本面与投资逻辑**：基于资产类型（基金/股票/ETF/债券等）和代码，分析该资产的投资逻辑是否仍然成立。如果是股票，关注行业趋势与估值；如果是基金/ETF，关注跟踪标的和费率合理性。
2. **盈亏分析**：结合成本价、现价、盈亏比例和上述止损止盈规则，判断当前是否处于合理区间。
3. **仓位与组合适配**：结合组合全貌，评估该资产的仓位大小、与其他持仓的相关性、行业/平台集中度风险。
4. **层级适配性**：该资产是否适合其所在的投资层级，是否应该调整层级。
5. **历史走势解读**：基于提供的历史市值数据，分析趋势。
6. **交易记录分析**：基于买卖记录，分析交易行为是否合理。
7. **综合建议**：给出具体的操作建议，以及未来的关注要点。

## 输出格式

请返回纯 JSON（不要 markdown 代码块），格式如下：
{
  "signal": "buy|sell|hold|watch",
  "signal_reason": "一句话核心理由",
  "risk_level": "low|medium|high|critical",
  "score": 85,
  "analysis": {
    "fundamentals": "基本面与投资逻辑分析（2-4句）",
    "pnl_assessment": "盈亏状态评估（2-3句）",
    "position_size": "仓位与组合适配评价（2-3句，需引用占比数据和同类持仓）",
    "layer_fit": "层级适配性（1-2句）",
    "trend": "走势趋势分析（2-3句）",
    "trading_behavior": "交易行为评价（1-2句）"
  },
  "action_plan": "具体操作建议，包含条件触发点（3-5句）",
  "risks": ["风险点1", "风险点2"],
  "highlights": ["亮点1", "亮点2"]
}

## 注意事项
- score 是 0-100 的整数，代表该资产的健康度/投资价值。
- 分析必须结合组合整体情况，不要孤立看待单个资产。
- 分析应当包含具体的数值引用，不要泛泛而谈。
- 如果信息不足以做出判断，明确说明并给出保守建议。
- 对于已清仓资产，重点分析历史表现和经验教训。
"""


ASSET_SEARCH_PROMPT = """你是一位顶级的宏观策略分析师与个人财富顾问（CFA 持证专家）。
结合当下的真实宏观市场环境以及客户的整个资产组合配置，请为客户推荐用来再平衡的优质标的，并提供完整的投资执行策略。

## 投资原则（必须严格遵守）
客户采用五层资产配置体系（安全垫/债券核心/股票核心/另类对冲/卫星机会）。
- **极度厌恶风险与雷区**：绝不推荐带有高杠杆、P2P、ST股或底层资产不透明的问题资产。
- **5% 单票上限红线**：若推荐单只股票或高波动资产，务必在策略中强调初始仓位不能超过总资产比例的 5%。如果客户当前总组合已经超配某一行业，必须尽量避免推荐相关性高的资产以分散风险。
- **允许加仓优质标的**：你既可以推荐客户未持有的优质新资产，也可以直接推荐加仓其当前已持有的资产（前提是它在当前宏观环境下仍是极佳选择，且不违反单票上限）。
- **层级相符**：
  - 第一层（安全垫/干火药）：以货币基金、短债基金为主，要求极度安全、高流动性。
  - 第二层（债券核心）：以纯债基金、利率债或高等级信用债为主，提供稳定生息。
  - 第三层（股票核心）：以宽基指数ETF为主（如沪深300、标普500、纳指100）或长期稳定高分红。
  - 第四层（另类/对冲）：如黄金ETF、资源类、或者与股市相关度极低的资产，抵抗周期。
  - 第五层（卫星机会）：提供高赔率、高弹性的行业主题、个股等，接受较大波动。

## 输出格式 (必须为纯 JSON，勿带 Markdown ``` json 包裹)
{
  "macro_analysis": "（必填）基于[Today's Date]前后的全球宏观或中国宏观经济周期、利率环境与预期，仅用简练的 2-4 句话分析当前为何这层资产值得买入。",
  "recommendations": [
    {
      "name": "资产名称 (如 标普500ETF)",
      "code": "代码 (如 513500.SH)",
      "type": "资产属类 (如 跨境ETF)",
      "rationale": "核心推荐逻辑，直击要害 (约 2-3 句话)",
      "downside_risk": "该资产如果遇到最差情况，预期的最大回撤幅度是多少，什么情况下会发生暴跌？",
      "execution_strategy": "建仓策略：例如『当价格回调至 XX 以下时分 3 个月定投建仓』或『底层资产较为稳健，可一笔买入』。",
      "risk_level": "low|medium|high|critical",
      "conviction_score": 整数值 (1-100之间，代表你对这个标的在当前宏观下的信心分)
    }
  ],
  "allocation_advice": "针对这笔具体资金（配置金额）应当如何划分在上述推荐标的上，给出一句综合配置建议。"
}
"""

HISTORY_SUMMARY_PROMPT = """你是一位专业的个人投资顾问（CFA 持证），正在为客户总结其历史资产变动情况。
你将收到客户过去一段时间内（多个历史快照）的资产总值、各层级资产分布，以及该期间发生的净资金流入/流出数据。

## 评估维度

请基于提供的数据，总结资产总值发生变化的核心原因，具体包括：
1. **净投入 vs 投资盈亏**：资产的增长/减少，有多少是由客户的净资金转入/转出造成的，有多少是纯粹的市场投资盈亏造成的？
2. **驱动层级分析**：观察各层级资产市值的变化，指出哪些层级（如股票核心、卫星机会、另类对冲等）是导致盈利或亏损的主要驱动力。
3. **趋势点评**：对整体资产的走势给出简短的专业点评和鼓励，若回撤较大则给出安抚与风险提示。

## 输出格式

请直接返回一段排版清晰、便于阅读的纯文本（可以使用换行和简单的列表符号，如 -，不要使用复杂的 Markdown 标题、不要使用 HTML，不要包含任何前言或后语，直接输出总结正文）。字数控制在 200 - 300 字左右。
"""


def _build_asset_context(asset_info, transactions, value_history,
                         layers_data=None, holdings_data=None, total_value=0):
    """将单个资产的数据格式化为 LLM 可理解的文本上下文，含完整组合信息"""
    lines = []

    lines.append("## 目标资产信息")
    lines.append(f"- 名称: {asset_info['name']}")
    if asset_info.get('code'):
        lines.append(f"- 代码: {asset_info['code']}")
    lines.append(f"- 类型: {asset_info.get('asset_type_display', '未知')}")
    lines.append(f"- 平台: {asset_info.get('platform', '未知')}")
    lines.append(f"- 所属层级: {asset_info.get('layer_name', '未知')}")
    lines.append(f"- 状态: {'当前持有' if asset_info.get('is_active') else '已清仓'}")
    lines.append("")

    if asset_info.get('is_active'):
        lines.append("## 当前持仓")
        lines.append(f"- 市值: ¥{asset_info['market_value']:,.0f}")
        lines.append(f"- 数量: {asset_info['quantity']:.2f} 份")
        if asset_info.get('cost_price'):
            lines.append(f"- 成本价: ¥{asset_info['cost_price']:.4f}")
        if asset_info.get('current_price'):
            lines.append(f"- 现价: ¥{asset_info['current_price']:.4f}")
        lines.append(f"- 持仓盈亏: ¥{asset_info['profit_loss']:+,.0f} ({asset_info['profit_loss_pct']:+.1f}%)")
        if asset_info.get('pct_of_total'):
            lines.append(f"- 占总资产比例: {asset_info['pct_of_total']:.1f}%")
        lines.append("")

    # Full portfolio context
    if total_value > 0:
        portfolio_text = _build_portfolio_context(layers_data or [], holdings_data or [], total_value)
        lines.append(portfolio_text)
        lines.append("")

    if value_history:
        lines.append("## 历史市值走势")
        for v in value_history:
            lines.append(f"- {v['date']}: 市值 ¥{v['market_value']:,.0f}, 盈亏 ¥{v['profit_loss']:+,.0f}")
        lines.append("")

    if transactions:
        lines.append("## 交易记录")
        for tx in transactions:
            qty_str = f", 数量 {tx['quantity']:.2f}" if tx.get('quantity') else ""
            price_str = f", 价格 ¥{tx['price']:.4f}" if tx.get('price') else ""
            pnl_str = f", 已实现盈亏 ¥{tx['realized_pnl']:+,.0f}" if tx.get('realized_pnl') else ""
            lines.append(f"- {tx['date']} {tx['action_display']}: ¥{tx['amount']:,.0f}{qty_str}{price_str}{pnl_str}")
        lines.append("")

    return "\n".join(lines)


def evaluate_asset(asset_info, transactions, value_history,
                   layers_data=None, holdings_data=None, total_value=0):
    """
    调用大模型对单个资产进行深度评估。

    Returns:
        dict: {success: bool, data: {...}, error: str}
    """
    config = _get_advisor_config()

    context = _build_asset_context(
        asset_info, transactions, value_history,
        layers_data=layers_data, holdings_data=holdings_data, total_value=total_value,
    )
    user_message = f"{context}\n\n请结合该资产的详细数据和组合全貌，给出你的深度评估。"

    try:
        if config['provider'] == 'anthropic':
            return _call_anthropic(user_message, config, system_prompt=ASSET_EVAL_PROMPT)
        else:
            return _call_openai_compatible(user_message, config, system_prompt=ASSET_EVAL_PROMPT)
    except Exception as e:
        logger.exception("evaluate_asset failed")
        return {'success': False, 'data': None, 'error': str(e)}


def search_and_recommend_assets(layer_name, buy_amount, current_holdings, portfolio_summary=None):
    """
    调用大模型为指定层级推荐资产。
    
    Args:
        layer_name: 目标层级名称
        buy_amount: 计划买入金额
        current_holdings: list of dict, 当前该层已持有资产信息
        portfolio_summary: list of dict, 全盘资产概览
        
    Returns:
        dict: {success: bool, data: {...}, error: str}
    """
    config = _get_advisor_config()
    today_str = date.today().isoformat()
    
    holdings_text = "该层暂无持仓"
    if current_holdings:
        holdings_text = ", ".join(f"{h.get('name')} (市值: ¥{float(h.get('market_value', 0)):,.0f})" for h in current_holdings)
        
    portfolio_text = "未提供全盘持仓"
    if portfolio_summary:
        limit = 15
        top_holdings = sorted(portfolio_summary, key=lambda x: x.get('value', 0), reverse=True)[:limit]
        p_list = [f"{h['name']}({h['layer']}, ¥{h['value']:,.0f})" for h in top_holdings]
        portfolio_text = "前几大持股占比分布: " + ", ".join(p_list) + f"等{len(portfolio_summary)}个资产。"
        
    user_message = (
        f"今天是 {today_str}。请以当前的宏观环境进行严肃分析。\n\n"
        f"客户总体投资组合概况 (防止超配): {portfolio_text}\n"
        f"本次目标配置层级: {layer_name}\n"
        f"计划投入金额: ¥{float(buy_amount):,.0f}\n"
        f"当前在 {layer_name} 层已持有资产: {holdings_text}\n\n"
        f"请按照前述输出格式推荐2-3个顶级标的。你可以推荐未持有的新资产，也完全可以建议加仓上述【当前已持有资产】（如果它们依然是最优选择）。"
        f"务必充分考虑客户在全组合中是否过度集中于特定风险。"
    )
    
    try:
        if config['provider'] == 'anthropic':
            return _call_anthropic(user_message, config, system_prompt=ASSET_SEARCH_PROMPT)
        else:
            return _call_openai_compatible(user_message, config, system_prompt=ASSET_SEARCH_PROMPT)
    except Exception as e:
        logger.exception("search_and_recommend_assets failed")
        return {'success': False, 'data': None, 'error': str(e)}


def _build_portfolio_context(layers_data, holdings_data, total_value):
    """将持仓数据格式化为 LLM 可理解的文本上下文"""
    lines = []
    lines.append(f"## 投资组合概览")
    lines.append(f"总资产: ¥{total_value:,.0f}")
    lines.append("")

    lines.append("## 层级配置")
    for ld in layers_data:
        lines.append(
            f"- {ld['name']}: 目标 {ld['target_ratio']}% | "
            f"实际 {ld['actual_ratio']}% | "
            f"偏差 {ld['deviation']:+.1f}% | "
            f"市值 ¥{ld['actual_value']:,.0f}"
        )
    lines.append("")

    lines.append("## 所有持仓明细")
    for h in holdings_data:
        pct_of_total = (h['market_value'] / total_value * 100) if total_value > 0 else 0
        pl_str = f"{h['profit_loss']:+,.0f} ({h['profit_loss_pct']:+.1f}%)" if h['profit_loss'] else "无盈亏数据"
        lines.append(
            f"- 【{h['layer_name']}】{h['name']}"
            f"{'（' + h['code'] + '）' if h['code'] else ''}"
            f" | 类型: {h['asset_type_display']}"
            f" | 平台: {h['platform'] or '未知'}"
            f" | 市值: ¥{h['market_value']:,.0f} (占比 {pct_of_total:.1f}%)"
            f" | 盈亏: {pl_str}"
        )

    return "\n".join(lines)


def _get_llm_config(purpose='advisor'):
    if purpose == 'ocr':
        return {
            'provider': Setting.get('llm_provider', 'openai_compatible'),
            'api_url': Setting.get('llm_api_url', ''),
            'api_key': Setting.get('llm_api_key', ''),
            'model': Setting.get('llm_model', ''),
            'max_tokens': int(Setting.get('llm_max_tokens', '2048')),
        }
    return {
        'provider': Setting.get('advisor_llm_provider', 'openai_compatible'),
        'api_url': Setting.get('advisor_api_url', ''),
        'api_key': Setting.get('advisor_api_key', ''),
        'model': Setting.get('advisor_model', ''),
        'max_tokens': int(Setting.get('advisor_max_tokens', '8192')),
    }


def _get_advisor_config():
    """获取 AI 顾问专用的 LLM 配置，与 OCR 配置独立。"""
    return _get_llm_config('advisor')


def evaluate_portfolio(layers_data, holdings_data, total_value):
    """
    调用大模型对投资组合进行评估。

    Returns:
        dict: {success: bool, data: {...}, error: str}
    """
    config = _get_advisor_config()

    portfolio_context = _build_portfolio_context(layers_data, holdings_data, total_value)
    user_message = f"{portfolio_context}\n\n请根据以上数据给出你的专业评估。"

    try:
        if config['provider'] == 'anthropic':
            return _call_anthropic(user_message, config)
        else:
            return _call_openai_compatible(user_message, config)
    except Exception as e:
        logger.exception("evaluate_portfolio failed")
        return {'success': False, 'data': None, 'error': str(e)}


def generate_history_summary(purpose='advisor'):
    """
    调用大模型对历史快照进行总结。
    """
    from ..models import Snapshot, Transaction
    from django.db.models import Sum

    config = _get_llm_config(purpose)
    
    # 获取最近 12 个快照（按时间正序排列以便模型理解时间线）
    snapshots = list(Snapshot.objects.order_by('-date')[:12])
    snapshots.reverse()
    
    if len(snapshots) < 2:
        return {'success': False, 'data': None, 'error': '快照数量不足，无法生成历史总结（至少需要2个快照）'}

    lines = ["## 历史快照与资金流动数据"]
    
    for i in range(len(snapshots)):
        s = snapshots[i]
        date_str = s.date.strftime('%Y-%m-%d')
        lines.append(f"\n### 快照 {date_str}")
        lines.append(f"- 总资产: ¥{s.total_value:,.0f}")
        if s.layer_values:
            lines.append("- 层级分布:")
            for k, v in s.layer_values.items():
                lines.append(f"  - {k}: ¥{v:,.0f}")
        
        # 如果不是最后一个快照，计算到下一个快照之间的净流入/流出
        if i < len(snapshots) - 1:
            next_s = snapshots[i+1]
            start_date = s.date.date() if hasattr(s.date, 'date') else s.date
            end_date = next_s.date.date() if hasattr(next_s.date, 'date') else next_s.date
            
            flows = Transaction.objects.filter(
                date__gt=start_date, date__lte=end_date,
                action__in=['transfer', 'withdraw'],
            )
            transfers = flows.filter(action='transfer').aggregate(t=Sum('amount'))['t'] or 0
            withdrawals = flows.filter(action='withdraw').aggregate(t=Sum('amount'))['t'] or 0
            net_flow = transfers - withdrawals
            
            lines.append(f"\n=> 期间资金净流动 ({start_date} 至 {end_date}): {'+' if net_flow >= 0 else ''}¥{net_flow:,.0f}")

    user_message = "\n".join(lines) + "\n\n请根据以上数据，生成历史资产变动的总结报告。"

    try:
        if config['provider'] == 'anthropic':
            return _call_anthropic(user_message, config, system_prompt=HISTORY_SUMMARY_PROMPT, expect_json=False)
        else:
            return _call_openai_compatible(user_message, config, system_prompt=HISTORY_SUMMARY_PROMPT, expect_json=False)
    except Exception as e:
        logger.exception("generate_history_summary failed")
        return {'success': False, 'data': None, 'error': str(e)}


def _call_anthropic(user_message, config, system_prompt=None, expect_json=True):
    api_key = config['api_key']
    api_url = config.get('api_url', '')

    if not api_key and not api_url:
        return {'success': False, 'data': None, 'error': '未配置顾问 AI 模型，请在设置中配置'}

    model = config['model'] or 'claude-sonnet-4-20250514'

    kwargs = {'api_key': api_key or 'not-needed'}
    if api_url:
        kwargs['base_url'] = api_url
    client = anthropic.Anthropic(**kwargs)
    last_err = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=config.get('max_tokens', 8192),
                system=system_prompt or ADVISOR_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text
            if expect_json:
                return _parse_response(text)
            else:
                return {'success': True, 'data': text.strip(), 'error': ''}
        except getattr(anthropic, 'APIStatusError', Exception) as e:
            if getattr(e, 'status_code', 0) in (429, 500, 502, 503, 504):
                last_err = e
                time.sleep(2 * (attempt + 1))
            else:
                return {'success': False, 'data': None, 'error': f'Anthropic API报错: {str(e)}'}
        except Exception as e:
            last_err = e
            time.sleep(1 * (attempt + 1))

    return {'success': False, 'data': None, 'error': f'网络请求持续失败，可能由于代理或防火墙引起 (已重试3次): {str(last_err)}'}


def _call_openai_compatible(user_message, config, system_prompt=None, expect_json=True):
    api_url = config['api_url']
    api_key = config['api_key']
    model = config['model']

    if not api_url:
        return {'success': False, 'data': None, 'error': '未配置顾问 API 地址，请在设置中配置'}

    url = api_url.rstrip('/')
    if not url.endswith('/chat/completions'):
        if url.endswith('/v1') or url.endswith('/v4') or url.endswith('/openai'):
            url += '/chat/completions'
        elif not url.endswith('/completions'):
            url += '/v1/chat/completions'

    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    payload = {
        'model': model or 'default',
        'messages': [
            {'role': 'system', 'content': system_prompt or ADVISOR_PROMPT},
            {'role': 'user', 'content': user_message},
        ],
        'max_tokens': config.get('max_tokens', 8192),
        'temperature': 0.3,
    }

    last_err = None
    for attempt in range(3):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
            resp.raise_for_status()
            result = resp.json()
            text = result['choices'][0]['message']['content']
            if expect_json:
                return _parse_response(text)
            else:
                return {'success': True, 'data': text.strip(), 'error': ''}
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503, 504):
                last_err = e
                time.sleep(2 * (attempt + 1))
            else:
                return {'success': False, 'data': None, 'error': f'API 报错 (HTTP {e.response.status_code}): {e.response.text[:200]}'}
        except Exception as e:
            last_err = e
            time.sleep(1 * (attempt + 1))

    return {'success': False, 'data': None, 'error': f'网络连接被异常断开，可能受当地代理软件、节点或防火墙影响 (已重试3次): {str(last_err)}'}


def _parse_response(text):
    """解析 LLM 返回的 JSON 文本"""
    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith('```'):
        first_newline = cleaned.index('\n')
        cleaned = cleaned[first_newline + 1:]
    if cleaned.endswith('```'):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        return {'success': True, 'data': data, 'error': ''}
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end != -1:
            try:
                data = json.loads(cleaned[start:end + 1])
                return {'success': True, 'data': data, 'error': ''}
            except json.JSONDecodeError:
                pass
        return {
            'success': False,
            'data': None,
            'error': f'无法解析模型返回的 JSON：{text[:200]}',
        }
