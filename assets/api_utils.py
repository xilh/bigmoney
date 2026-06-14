"""
API 通用错误处理与响应工具。

提供：
- ApiError: 业务异常基类，携带错误码与用户友好消息
- api_endpoint: 装饰器，捕获异常返回结构化 JSON，不暴露内部 traceback

错误响应格式：
{
    "success": false,
    "code": "INVALID_INPUT",      # 机器可读
    "message": "卖出数量不能为负",  # 用户友好
    "detail": "..."                # 调试用（仅 DEBUG 模式）
}
"""
import functools
import json
import logging
from decimal import InvalidOperation

from django.conf import settings
from django.http import Http404, JsonResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """业务层异常：携带错误码、用户消息、HTTP 状态码"""
    def __init__(self, code: str, message: str, status: int = 400, detail: str = ''):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


# 常用错误的工厂
def invalid_input(message: str, detail: str = '') -> ApiError:
    return ApiError('INVALID_INPUT', message, 400, detail)


def not_found(message: str = '资源不存在') -> ApiError:
    return ApiError('NOT_FOUND', message, 404)


def conflict(message: str) -> ApiError:
    return ApiError('CONFLICT', message, 409)


def internal_error(message: str = '服务内部错误', detail: str = '') -> ApiError:
    return ApiError('INTERNAL_ERROR', message, 500, detail)


def _build_error_response(code: str, message: str, status: int, detail: str = '') -> JsonResponse:
    body = {'success': False, 'code': code, 'message': message, 'error': message}
    if detail and getattr(settings, 'DEBUG', False):
        body['detail'] = detail
    return JsonResponse(body, status=status)


def api_endpoint(view_func):
    """
    装饰器：包装 API view，统一异常处理。

    在 view 中可以：
    - raise ApiError(code, message, status) 抛业务异常
    - raise invalid_input("...") 等工厂方法
    - 让 Http404 自然冒泡（get_object_or_404）
    - 其它未预期异常会被记录到日志并返回 INTERNAL_ERROR
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except ApiError as e:
            return _build_error_response(e.code, e.message, e.status, e.detail)
        except Http404 as e:
            return _build_error_response('NOT_FOUND', '请求的资源不存在', 404, str(e))
        except json.JSONDecodeError as e:
            return _build_error_response('INVALID_INPUT', '请求数据不是合法 JSON', 400, str(e))
        except (ValueError, InvalidOperation) as e:
            return _build_error_response('INVALID_INPUT', '输入数据格式错误', 400, str(e))
        except KeyError as e:
            return _build_error_response('INVALID_INPUT', f'缺少必需字段: {e}', 400, str(e))
        except Exception as e:
            # 未预期异常：记录到日志，对外只返回通用消息，避免泄露内部细节
            logger.exception("Unhandled exception in %s", view_func.__name__)
            return _build_error_response(
                'INTERNAL_ERROR', '服务出现内部错误，请稍后重试或联系管理员', 500,
                f'{type(e).__name__}: {e}'
            )
    return wrapper
