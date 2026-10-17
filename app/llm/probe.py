"""Local developer probe. Never reads student records; no automatic retries."""
from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
import os
from pathlib import Path

from pydantic import ValidationError

from app.llm.provider import GenerationRequest, Message, ProviderConfig, ProviderError, create_provider


def load_config() -> ProviderConfig:
    # Deliberately separate from legacy RSI_LLM_* settings.
    return ProviderConfig(
        vendor=os.getenv("WENJIN_PROVIDER", "deepseek"),
        model=os.getenv("WENJIN_MODEL", "deepseek-flash"),
        base_url=os.getenv("WENJIN_PROVIDER_URL", "https://api.deepseek.com"),
        api_key=os.getenv("WENJIN_PROVIDER_KEY", ""),
        timeout_seconds=float(os.getenv("WENJIN_PROVIDER_TIMEOUT", "30")),
        max_output_tokens=128,
    )


async def probe(config: ProviderConfig):
    result = await create_provider(config).generate(GenerationRequest(messages=(
        Message(role="user", content="开发连通测试：请只回答 2+3 的计算结果。"),
    )))
    # Model response text is deliberately excluded from terminal output.
    print({"provider": result.provider, "requested_model": result.requested_model,
           "returned_model": result.returned_model, "finish_reason": result.finish_reason,
           "usage": result.usage.model_dump(), "elapsed_ms": result.elapsed_ms,
           "simulated": result.simulated, "has_text": bool(result.text.strip())})


def main():
    parser = argparse.ArgumentParser(description="仅发送固定开发文本，不读取数据库；输出上限128 tokens，无重试")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        from app.config import _load_dotenv
        _load_dotenv(args.env_file)
    if not os.getenv("WENJIN_PROVIDER_KEY") and os.getenv("WENJIN_PROVIDER", "deepseek") != "fake":
        os.environ["WENJIN_PROVIDER_KEY"] = getpass("Provider API key (hidden, process only): ")
    try:
        asyncio.run(probe(load_config()))
    except ProviderError as error:
        print("Provider test failed: " + error.code)
        raise SystemExit(1) from None
    except (ValueError, ValidationError):
        print("Provider configuration invalid; check local configuration (values hidden).")
        raise SystemExit(2) from None
    finally:
        os.environ.pop("WENJIN_PROVIDER_KEY", None)


if __name__ == "__main__":
    main()
