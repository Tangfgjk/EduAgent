"""Private, account-owned API models. Secrets never enter workspace catalogs."""

from urllib.parse import urlsplit

from deeptutor.multi_user.paths import get_owner_secrets_dir
from deeptutor.services.config.model_catalog import ModelCatalogService
from deeptutor.utils.secret_files import ensure_private_directory, ensure_private_file

PROFILE_ID = "wenjin-personal-api"


def service():
    path = get_owner_secrets_dir() / "api-models" / "catalog.json"
    return ModelCatalogService.get_instance(path)


def profiles():
    catalog = service()
    if not catalog.path.exists():
        return []
    return [
        p
        for p in catalog.load().get("services", {}).get("llm", {}).get("profiles", [])
        if p.get("id") == PROFILE_ID
    ]


def view():
    values = profiles()
    if not values:
        return {"configured": False}
    p = values[0]
    return {
        "configured": True,
        "provider": p["binding"],
        "base_url": p.get("base_url", ""),
        "model": p["models"][0]["model"],
        "has_api_key": bool(p.get("api_key")),
    }


def save(provider, base_url, model, api_key):
    if provider not in {"openai", "deepseek", "anthropic", "gemini", "dashscope", "moonshot"}:
        raise ValueError("请选择支持的提供方")
    if base_url:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("服务地址须为 HTTPS 地址，不能包含密钥或查询参数")
    current = profiles()
    if (
        current
        and not api_key
        and (current[0].get("binding") != provider or current[0].get("base_url", "") != base_url)
    ):
        raise ValueError("更换提供方或地址时请重新填写 API Key")
    key = api_key.strip() if api_key else (current[0].get("api_key", "") if current else "")
    if not key:
        raise ValueError("首次配置必须填写 API Key")
    if not model.strip():
        raise ValueError("请填写模型 ID")
    profile = {
        "id": PROFILE_ID,
        "name": "我的模型",
        "binding": provider,
        "base_url": base_url,
        "api_key": key,
        "owner_bound": True,
        "managed_by": "wenjin_personal_api",
        "models": [{"id": "personal-model", "name": model.strip(), "model": model.strip()}],
    }
    catalog = service()
    ensure_private_directory(catalog.path.parent)
    catalog.save(
        {
            "version": 1,
            "services": {
                "llm": {
                    "profiles": [profile],
                    "active_profile_id": PROFILE_ID,
                    "active_model_id": "personal-model",
                }
            },
        }
    )
    ensure_private_file(catalog.path)
    return view()


def remove():
    service().path.unlink(missing_ok=True)
    return {"configured": False}
