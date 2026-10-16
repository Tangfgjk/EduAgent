"""LLM 抽象层：OpenAI 兼容协议（默认智谱 GLM）+ 结构化 JSON 输出 + 测试用 FakeLLM。

设计约束（docs/platform 计划）：不引 SDK，httpx 直连；结构化输出 = 提示词约束 JSON +
容错解析 + Pydantic 校验，校验失败把错误回喂重试一次，仍失败抛 LLMError（调用方按
"宁缺勿脏"丢弃，见 docs/02 设计规则④）。
"""
from __future__ import annotations

import json
import re
from typing import Callable, Iterable

from httpx import Client, HTTPError
from pydantic import BaseModel, ValidationError


class LLMError(RuntimeError):
    """LLM 调用或结构化解析最终失败（含网络/超时——上层统一按降级处理）。"""


def extract_json(text: str) -> str:
    """容错提取 JSON：先原样，再剥 ``` 围栏，再截取首个 { 到末个 }。"""
    text = text.strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    raise LLMError(f"无法从模型输出中提取 JSON: {text[:200]!r}")


class BaseLLM:
    """LLM 客户端协议：complete 返回文本；complete_json 返回校验后的模型实例。"""

    def complete(self, messages: list[dict], temperature: float = 0.6) -> str:
        raise NotImplementedError

    def complete_json(
        self,
        messages: list[dict],
        model_cls: type[BaseModel],
        temperature: float = 0.2,
        retries: int = 1,
    ) -> BaseModel:
        schema_hint = f"只输出一个 JSON 对象，不要任何解释或围栏。字段结构：{model_cls.model_json_schema()}"
        convo = list(messages) + [{"role": "system", "content": schema_hint}]
        last_err: Exception | None = None
        for _ in range(retries + 1):
            raw = self.complete(convo, temperature=temperature)
            try:
                return model_cls.model_validate_json(extract_json(raw))
            except (LLMError, ValidationError, json.JSONDecodeError) as err:
                last_err = err
                convo = convo + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"上面的输出不符合 JSON 规范：{err}。请重新只输出合法 JSON。"},
                ]
        raise LLMError(f"结构化输出在重试后仍失败: {last_err}")


class OpenAICompatClient(BaseLLM):
    """OpenAI 兼容 chat/completions 客户端（智谱 GLM / 任意兼容端点）。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 180.0,
                 extra_body: dict | None = None):
        self.model = model
        self.extra_body = extra_body or {}   # 透传服务端私有字段（如 sglang chat_template_kwargs）
        self._client = Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    def complete(self, messages: list[dict], temperature: float = 0.6) -> str:
        convo = list(messages)
        last_err: Exception | None = None
        for attempt in range(2):  # 传输层瞬断重试一次
            try:
                resp = self._client.post(
                    "/chat/completions",
                    json={"model": self.model, "messages": convo,
                          "temperature": temperature, **self.extra_body},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"] or ""
            except HTTPError as err:       # 超时/连接错误 → LLMError（调用方降级）
                last_err = err
                if attempt == 0:
                    continue
                raise LLMError(f"LLM 传输失败: {err}") from err
            except (KeyError, IndexError, TypeError) as err:
                raise LLMError(f"响应结构异常: {err}") from err
        raise LLMError(f"LLM 调用失败: {last_err}")


class FakeLLM(BaseLLM):
    """脚本化假 LLM：按入队顺序弹出响应；响应可为字符串、Pydantic 实例或 callable(messages)->str。"""

    def __init__(self, scripted: Iterable[str | BaseModel | Callable[[list[dict]], str]] | None = None):
        self.scripted: list = list(scripted or [])
        self.calls: list[list[dict]] = []

    def queue(self, *responses) -> None:
        self.scripted.extend(responses)

    def complete(self, messages: list[dict], temperature: float = 0.6) -> str:
        self.calls.append(messages)
        if not self.scripted:
            return "{}"  # 默认返回空 JSON，感知器等按"宁缺勿脏"丢弃
        item = self.scripted.pop(0)
        if callable(item):
            return item(messages)
        if isinstance(item, BaseModel):
            return item.model_dump_json()
        return item
