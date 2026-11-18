"use client";
import { useEffect, useState } from "react";
import { requestJson } from "@/shared/api/client";
import { useCapabilityAccess } from "@/components/access/CapabilityAccessContext";

type Config = {configured: boolean; provider?: string; base_url?: string; model?: string};
export default function PersonalModelsSection() {
  const [config, setConfig] = useState<Config>({configured:false});
  const [provider, setProvider] = useState("deepseek");
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const { refresh } = useCapabilityAccess();
  useEffect(() => { requestJson<Config>("/api/settings/personal-model").then(c => {
    setConfig(c); setProvider(c.provider || "deepseek"); setUrl(c.base_url || ""); setModel(c.model || "");
  }).catch(() => setMessage("配置加载失败，请刷新重试")).finally(() => setBusy(false)); }, []);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      setConfig(await requestJson<Config>("/api/settings/personal-model", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({provider, base_url:url, model, api_key:key || null})}));
      setKey(""); await refresh(); setMessage("已保存。陪学与自动规划将使用你的模型；可以返回学习开始对话。");
    } catch { setMessage("保存失败，请检查服务地址、模型 ID 和 API Key。"); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm("删除自己的模型配置？删除后需重新配置才能使用陪学。")) return;
    setBusy(true);
    try { setConfig(await requestJson<Config>("/api/settings/personal-model", {method:"DELETE"})); setKey(""); await refresh(); setMessage("已删除个人配置"); }
    catch { setMessage("删除失败，请重试"); } finally { setBusy(false); }
  }
  const input = "mt-2 w-full rounded-xl border border-[var(--border)] bg-[var(--background)] p-3";
  return <section className="max-w-2xl space-y-6">
    <header><h1 className="text-2xl font-semibold">我的模型</h1><p className="mt-3 text-sm text-[var(--muted-foreground)]">配置自己的提供方和 API Key，用于陪学及训练后的自动规划。模型调用费用由你的提供方收取。</p></header>
    <form onSubmit={save} className="space-y-5 rounded-2xl border border-[var(--border)] p-6">
      <fieldset disabled={busy} className="space-y-5">
        <label className="block">提供方<select className={input} value={provider} onChange={e=>setProvider(e.target.value)}>{["deepseek","openai","anthropic","gemini","dashscope","moonshot"].map(p=><option key={p} value={p}>{p}</option>)}</select></label>
        <label className="block">服务地址（可选）<input type="url" className={input} value={url} onChange={e=>setUrl(e.target.value)} placeholder="留空使用提供方默认地址；自定义地址使用 HTTPS" /></label>
        <label className="block">模型 ID<input required className={input} value={model} onChange={e=>setModel(e.target.value)} placeholder="例如 deepseek-chat" /></label>
        <label className="block">API Key<input type="password" autoComplete="new-password" required={!config.configured} className={input} value={key} onChange={e=>setKey(e.target.value)} placeholder={config.configured ? "已保存，留空保持现有密钥" : "填写你自己的 API Key"} /></label>
        <p className="text-sm text-[var(--muted-foreground)]">密钥只保存在服务端个人私密目录，页面不会显示已保存的密钥。</p>
        <div className="flex gap-3"><button type="submit" className="rounded-xl bg-[var(--primary)] px-4 py-2 text-[var(--primary-foreground)]">{busy ? "处理中…" : "保存配置"}</button>{config.configured && <button type="button" onClick={remove}>删除配置</button>}</div>
      </fieldset>
    </form>
    {message && <p role="status">{message}</p>}
    <a href="/learning" className="inline-block underline">返回学习</a>
  </section>;
}
