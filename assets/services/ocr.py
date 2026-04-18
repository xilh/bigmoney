"""
截图识别服务
支持两种后端：
  1. Anthropic Claude API（官方）
  2. OpenAI 兼容 API（本地大模型如 Ollama、vLLM、LM Studio 等）
"""
import base64
import json
import logging
from pathlib import Path
import time

logger = logging.getLogger(__name__)

import anthropic
import httpx


# 识别提示词模板
RECOGNITION_PROMPT = """你是一个专业的金融数据提取助手。请分析这张持仓/理财截图，提取所有资产持仓信息。

## 已有数据（用于平台推断和产品名去重）

$EXISTING_DATA_BLOCK$

## 平台判断

请判断截图来自哪个 App/平台：
1. 从 UI 元素判断（App 图标、导航栏、配色、底部 Tab、状态栏标题等）
2. 如果 UI 不明确，看截图中的产品是否大部分出现在上面已有数据的某个平台下 —— 如果是，使用该平台名
3. 不要从单个产品名猜平台（如看到"招商白酒"不代表平台是"招商银行"）
4. 以上都不确定时填空字符串

## 去重匹配

将识别到的产品名称与已有数据对比。如果高度相似（个别字差异、多/少空格、简称vs全称），直接使用已有数据中的名称。

## 返回格式

返回纯 JSON 对象（不要 markdown 代码块）：
{
  "platform": "平台名",
  "holdings": [
    {
      "name": "资产名称",
      "code": "代码（无则空字符串）",
      "asset_type": "类型",
      "quantity": 份额数字,
      "cost_price": 成本价或null,
      "current_price": 现价/净值或null,
      "market_value": 市值,
      "profit_loss": 累计盈亏,
      "profit_loss_pct": 盈亏百分比,
      "suggested_layer": 层级1-5
    }
  ]
}

asset_type 枚举：cash, money_fund, bank_product, deposit, bond_fund, convertible_bond, index_fund, stock, etf, dividend_stock, gold, qdii, hk_stock, other

suggested_layer 规则：1=安全垫（现金/货基/银行理财R1-R2/存单） 2=债券（中短债/纯债/可转债） 3=股票核心（沪深300/红利股/A股） 4=另类对冲（黄金/港股/QDII） 5=卫星机会（行业ETF/主题/小盘）

## 提取规则

字段映射：
- "金额"/"持有金额"/"市值"/"最新市值" → market_value
- "持有收益"/"累计收益"/"浮动盈亏"/"持仓盈亏" → profit_loss（必须提取！）
- "昨日收益"/"日收益"/"今日收益" → 忽略（单日收益，非累计）
- "收益率"/"持有收益率" → profit_loss_pct
- "份额"/"持有份额" → quantity
- "净值"/"最新净值" → current_price
- "成本"/"成本价"/"买入均价" → cost_price

数字规则：精确提取含小数，"+"为正值，"-"为负值，逗号是千分位。profit_loss 有值时不能填 0。金额单位：人民币元。

请返回纯 JSON 对象："""


def _read_image(image_path: str, max_dimension: int = 800):
    """
    读取图片文件，压缩后返回 base64 编码和 MIME 类型。
    iPhone 截图通常 1170×2532，在此压缩到 800 左右可以把耗时减半。
    """
    from io import BytesIO
    from PIL import Image

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {path}")

    img = Image.open(path)

    # 获取原始尺寸
    orig_w, orig_h = img.size
    longest = max(orig_w, orig_h)

    # 如果超过 max_dimension，等比缩放
    if longest > max_dimension:
        ratio = max_dimension / longest
        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # 转为 RGB（去掉 alpha 通道，减小体积）
    if img.mode in ('RGBA', 'P', 'LA'):
        img = img.convert('RGB')

    # 编码为 JPEG（体积远小于 PNG，且对文字识别足够）
    buffer = BytesIO()
    img.save(buffer, format='JPEG', quality=75, optimize=True)
    image_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
    media_type = 'image/jpeg'

    return image_data, media_type


def _extract_json(text: str) -> dict:
    """
    从 AI 返回文本中提取 JSON。
    支持两种格式：
      1. {"platform": "...", "holdings": [...]} —— 新格式
      2. [...] —— 旧格式（纯数组）
    返回: {"platform": str, "holdings": list}
    """
    text = text.strip()

    import re
    # 1. 尝试匹配 Markdown 代码块 (```json ... ```)
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text, re.IGNORECASE)
    if match:
        json_text = match.group(1).strip()
    else:
        # 2. 如果没有代码块，找最外层的 {} 或 []
        start_obj = text.find('{')
        start_arr = text.find('[')
        
        start = -1
        if start_obj != -1 and start_arr != -1:
            start = min(start_obj, start_arr)
        elif start_obj != -1:
            start = start_obj
        else:
            start = start_arr
            
        if start != -1:
            if text[start] == '{':
                end = text.rfind('}')
            else:
                end = text.rfind(']')
                
            if end != -1 and end >= start:
                json_text = text[start:end+1]
            else:
                json_text = text[start:]
        else:
            json_text = text

    # 尝试直接解析
    parsed = _try_parse(json_text)
    if parsed is not None:
        return _normalize_result(parsed)

    # JSON 可能被截断，尝试修复
    repaired = _repair_truncated_json(json_text)
    if repaired is not None:
        return _normalize_result(repaired)

    raise json.JSONDecodeError("无法解析 JSON", text[:200], 0)


def _try_parse(text: str):
    """尝试解析 JSON，失败返回 None"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _normalize_result(data) -> dict:
    """统一返回格式为 {platform, holdings}"""
    if isinstance(data, dict):
        # 新格式: {platform, holdings}
        platform = data.get('platform', '')
        holdings = data.get('holdings', [])
        if not isinstance(holdings, list):
            holdings = [holdings] if holdings else []
        # 如果没有 holdings key，可能是单个持仓对象
        if not holdings and 'name' in data:
            platform = ''
            holdings = [data]
        return {'platform': platform, 'holdings': holdings}
    elif isinstance(data, list):
        # 旧格式: 纯数组
        return {'platform': '', 'holdings': data}
    else:
        return {'platform': '', 'holdings': []}


def _repair_truncated_json(text: str) -> list:
    """
    尝试修复被截断的 JSON 数组。
    找到最后一个完整的对象（以 } 结尾），截断后面的内容并补上 ]
    """
    # 找到所有 } 的位置
    brace_positions = []
    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                brace_positions.append(i)

    # 从最后一个完整的 } 截断
    for pos in reversed(brace_positions):
        candidate = text[:pos + 1]
        # 确保以 [ 开头
        start = candidate.find('[')
        if start == -1:
            continue
        candidate = candidate[start:]
        # 补上结尾的 ]
        if not candidate.rstrip().endswith(']'):
            # 可能最后一个 } 后面有逗号
            candidate = candidate.rstrip().rstrip(',')
            candidate += '\n]'
        try:
            data = json.loads(candidate)
            if isinstance(data, list) and len(data) > 0:
                return data
        except json.JSONDecodeError:
            continue

    return None


def _build_existing_data_block(existing_holdings: list[dict] | None) -> str:
    """构建已有数据块，按平台分组，嵌入到提示词中"""
    if not existing_holdings:
        return "（当前无已有持仓数据）"

    # Group products by platform
    from collections import defaultdict
    by_platform = defaultdict(set)
    all_names = set()
    for h in existing_holdings:
        name = h.get('name', '')
        platform = h.get('platform', '') or '未知平台'
        if name:
            by_platform[platform].add(name)
            all_names.add(name)

    if not all_names:
        return "（当前无已有持仓数据）"

    lines = ["系统中已有持仓（按平台分组）："]
    for platform, names in sorted(by_platform.items()):
        lines.append(f"  【{platform}】{', '.join(sorted(names))}")

    return '\n'.join(lines)


def _call_anthropic(image_data: str, media_type: str, api_key: str, model: str,
                    max_tokens: int = 2048, base_url: str = '',
                    prompt: str = '') -> str:
    """调用 Anthropic 协议 API（支持 Anthropic 官方、LM Studio 等兼容端点）"""
    kwargs = {'api_key': api_key or 'not-needed'}
    if base_url:
        kwargs['base_url'] = base_url
    client = anthropic.Anthropic(**kwargs)
    last_err = None
    for attempt in range(3):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt or RECOGNITION_PROMPT,
                            }
                        ],
                    }
                ],
            )
            return message.content[0].text
        except getattr(anthropic, 'APIStatusError', Exception) as e:
            if getattr(e, 'status_code', 0) in (429, 500, 502, 503, 504):
                last_err = e
                time.sleep(2 * (attempt + 1))
            else:
                raise e
        except Exception as e:
            last_err = e
            time.sleep(1 * (attempt + 1))

    logger.error("Anthropic API failed after 3 retries: %s", last_err)
    raise Exception(f'网络请求持续失败，可能由于代理或防火墙引起 (已重试3次): {str(last_err)}')


def _call_openai_compatible(image_data: str, media_type: str, api_key: str,
                            api_url: str, model: str, max_tokens: int = 2048,
                            prompt: str = '') -> str:
    """
    调用 OpenAI 兼容 API（支持 Ollama、vLLM、LM Studio、OpenAI 等）
    使用 OpenAI Chat Completions 格式，图片通过 base64 data URL 传递
    """
    # 确保 URL 以 /chat/completions 结尾
    url = api_url.rstrip('/')
    if not url.endswith('/chat/completions'):
        if url.endswith('/v1') or url.endswith('/v4') or url.endswith('/openai'):
            url += '/chat/completions'
        elif not url.endswith('/completions'):
            url += '/v1/chat/completions'

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data_url = f"data:{media_type};base64,{image_data}"

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt or RECOGNITION_PROMPT,
                    }
                ],
            }
        ],
    }

    last_err = None
    for attempt in range(3):
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=300.0)
            response.raise_for_status()
            result = response.json()

            # 提取内容：优先 content，如果为空则尝试 reasoning_content
            content = result["choices"][0]["message"].get("content", "")
            if not content or not content.strip():
                content = result["choices"][0]["message"].get("reasoning_content", "")

            return content
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503, 504):
                last_err = e
                time.sleep(2 * (attempt + 1))
            else:
                raise e
        except Exception as e:
            last_err = e
            time.sleep(1 * (attempt + 1))

    logger.error("OpenAI-compatible API failed after 3 retries: %s", last_err)
    raise Exception(f'网络连接被异常断开，可能受当地代理软件、节点或防火墙影响 (已重试3次): {str(last_err)}')


def recognize_screenshot(image_path: str, api_key: str,
                         provider: str = 'anthropic',
                         api_url: str = '',
                         model: str = '',
                         max_tokens: int = 2048,
                         existing_holdings: list[dict] | None = None) -> dict:
    """
    识别持仓截图

    Args:
        image_path: 图片文件路径
        api_key: API Key
        provider: 'anthropic' 或 'openai_compatible'
        api_url: API Base URL（openai_compatible 必填；anthropic 选填，填则覆盖默认端点）
        model: 模型名称
        max_tokens: 大模型返回的最大限制
        existing_holdings: 已有持仓列表，用于去重匹配

    Returns:
        dict: {"success": bool, "data": list, "platform": str, "error": str}
    """
    if provider == 'anthropic' and not api_key and not api_url:
        return {"success": False, "data": [], "platform": "", "error": "未配置 AI 模型，请在设置中配置"}
    if provider == 'openai_compatible' and not api_url:
        return {"success": False, "data": [], "platform": "", "error": "未配置 API 地址，请在设置中配置"}

    try:
        image_data, media_type = _read_image(image_path)

        # 构建包含已有数据的提示词
        data_block = _build_existing_data_block(existing_holdings)
        prompt = RECOGNITION_PROMPT.replace('$EXISTING_DATA_BLOCK$', data_block)

        if provider == 'anthropic':
            response_text = _call_anthropic(
                image_data, media_type, api_key,
                model or 'claude-sonnet-4-20250514',
                max_tokens=max_tokens,
                base_url=api_url,
                prompt=prompt,
            )
        else:
            response_text = _call_openai_compatible(
                image_data, media_type, api_key,
                api_url, model or 'gpt-4o',
                max_tokens=max_tokens,
                prompt=prompt,
            )

        result = _extract_json(response_text)
        return {
            "success": True,
            "data": result['holdings'],
            "platform": result['platform'],
            "error": ""
        }

    except FileNotFoundError as e:
        return {"success": False, "data": [], "platform": "", "error": str(e)}
    except json.JSONDecodeError as e:
        return {"success": False, "data": [], "platform": "", "error": f"AI 返回的数据格式异常，无法解析: {str(e)}"}
    except anthropic.APIError as e:
        return {"success": False, "data": [], "platform": "", "error": f"Anthropic API 调用失败: {str(e)}"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "data": [], "platform": "", "error": f"API 调用失败 (HTTP {e.response.status_code}): {e.response.text[:300]}"}
    except httpx.ConnectError:
        return {"success": False, "data": [], "platform": "", "error": f"无法连接到 API 地址: {api_url}，请检查服务是否启动"}
    except Exception as e:
        return {"success": False, "data": [], "platform": "", "error": f"识别过程出错: {str(e)}"}
