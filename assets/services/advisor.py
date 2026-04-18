"""
AI 投资顾问服务
使用大模型对持仓进行综合评估，给出买入/卖出/持有建议及风险预警。
复用 Setting 中的 LLM 配置（与 OCR 共享 provider 设置）。
"""
import json
import time

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


def _get_advisor_config():
    """获取 AI 顾问专用的 LLM 配置，与 OCR 配置独立。"""
    return {
        'provider': Setting.get('advisor_llm_provider', 'openai_compatible'),
        'api_url': Setting.get('advisor_api_url', ''),
        'api_key': Setting.get('advisor_api_key', ''),
        'model': Setting.get('advisor_model', ''),
        'max_tokens': int(Setting.get('advisor_max_tokens', '8192')),
    }


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
        return {'success': False, 'data': None, 'error': str(e)}


def _call_anthropic(user_message, config):
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
                system=ADVISOR_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text
            return _parse_response(text)
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


def _call_openai_compatible(user_message, config):
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
            {'role': 'system', 'content': ADVISOR_PROMPT},
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
            return _parse_response(text)
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
