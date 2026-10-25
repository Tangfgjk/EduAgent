"""Provider profiles expose no credentials. Model IDs are selected by the operator."""
import os
from pathlib import Path
from app.llm.provider import ProviderConfig,ProviderError,create_provider

PROFILES={
    'deepseek':('DeepSeek','deepseek','https://api.deepseek.com','WENJIN_DEEPSEEK_KEY'),
    'openai':('OpenAI','openai_compatible','https://api.openai.com/v1','WENJIN_OPENAI_KEY'),
    'anthropic':('Claude','anthropic','https://api.anthropic.com','WENJIN_ANTHROPIC_KEY'),
    'gemini':('Gemini','gemini','https://generativelanguage.googleapis.com','WENJIN_GEMINI_KEY'),
    'qwen':('Qwen / 百炼','openai_compatible','https://dashscope.aliyuncs.com/compatible-mode/v1','WENJIN_QWEN_KEY'),
    'glm':('GLM / 智谱','openai_compatible','https://open.bigmodel.cn/api/paas/v4','WENJIN_GLM_KEY'),
    'doubao':('豆包 / 火山方舟','openai_compatible','https://ark.cn-beijing.volces.com/api/v3','WENJIN_DOUBAO_KEY'),
    'moonshot':('Kimi / Moonshot','openai_compatible','https://api.moonshot.cn/v1','WENJIN_MOONSHOT_KEY'),
}


def credential(vendor):
    if vendor=='deepseek':
        from app.infrastructure.runtime.model_tasks import model_key
        return model_key()
    if vendor not in PROFILES:return ''
    value=os.getenv(PROFILES[vendor][3],'').strip()
    path=Path('.local/providers')/(vendor+'.key')
    return value or (path.read_text(encoding='utf-8').strip() if path.is_file() else '')


def catalog():
    return [{'vendor':key,'name':value[0],'protocol':value[1],'endpoint':endpoint_for(key),
        'key_configured':bool(credential(key)),'text_adapter_implemented':True,
        'live_validation':'development_record_available' if key=='deepseek' else 'not_run',
        'tools':False,'multimodal':False,'streaming':False} for key,value in PROFILES.items()]


def endpoint_for(vendor):
    if vendor == 'qwen':
        from app.modules.resources.media import api_host
        return api_host() + '/compatible-mode/v1'
    return PROFILES[vendor][2]


def provider(vendor,model,limit):
    if vendor not in PROFILES:raise ProviderError('unsupported_vendor')
    _,protocol,_,_=PROFILES[vendor]
    endpoint=endpoint_for(vendor)
    return create_provider(ProviderConfig(vendor=protocol,base_url=endpoint,model=model,api_key=credential(vendor),max_output_tokens=limit))
