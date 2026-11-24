"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, apiUrl } from "@/lib/api";
import { stripAudioMimeParameters } from "@/lib/voice-mime";

export type RecorderState = "idle" | "recording" | "transcribing";
const MAX_RECORDING_MS = 120_000;

/** Capture a bounded clip, transcribe it, and let the learner edit before sending. */
export function useVoiceRecorder(onTranscript: (text: string) => void) {
  const [state, setState] = useState<RecorderState>("idle");
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const busy = useRef(false);
  const mounted = useRef(true);
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;

  const release = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
  }, []);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") recorder.stop();
  }, []);

  const start = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    const token = ++generation.current;
    const current = () => mounted.current && generation.current === token;
    setError(null);
    const fail = (message: string) => {
      if (!current()) return;
      busy.current = false;
      setError(message);
      setState("idle");
    };
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      fail("麦克风需要安全连接，请通过 HTTPS 地址打开网站后再试。");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      fail("当前浏览器不支持录音，请使用新版 Edge、Chrome 或 Safari。");
      return;
    }
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const ready = await apiFetch(apiUrl("/api/voice/capabilities"), { signal: controller.signal });
      if (ready.ok) {
        const capabilities = await ready.json() as { stt?: { available?: boolean } };
        if (capabilities.stt?.available === false) {
          fail("语音识别尚未就绪，请到设置配置语音识别模型，或联系管理员安装内置语音。");
          return;
        }
      }
    } catch {
      fail("无法连接语音服务，请检查网络后重试。");
      return;
    }
    if (!current()) return;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      const name = e instanceof DOMException ? e.name : "";
      fail(name === "NotFoundError" ? "没有检测到麦克风，请先连接麦克风。" :
        name === "NotReadableError" ? "麦克风正被其他程序占用，请关闭后重试。" :
          "请在浏览器中允许麦克风权限后重试。");
      return;
    }
    if (!current()) { stream.getTracks().forEach(track => track.stop()); return; }
    streamRef.current = stream;
    let recorder: MediaRecorder;
    try {
      const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"]
        .find(value => MediaRecorder.isTypeSupported?.(value));
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    } catch {
      release(); fail("无法启动录音，请检查麦克风并重试。"); return;
    }
    const chunks: Blob[] = [];
    recorderRef.current = recorder;
    recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = async () => {
      release();
      recorderRef.current = null;
      if (!current()) return;
      const mime = stripAudioMimeParameters(recorder.mimeType || "audio/webm");
      const clip = new Blob(chunks, { type: mime });
      if (!clip.size) { fail("没有录到声音，请重新录制。"); return; }
      setState("transcribing");
      const timeout = setTimeout(() => controller.abort(), 90_000);
      try {
        const form = new FormData();
        const ext = mime.includes("ogg") ? "ogg" : mime.includes("mp4") ? "mp4" : "webm";
        form.append("file", clip, `recording.${ext}`);
        const response = await apiFetch(apiUrl("/api/voice/stt"), {
          method: "POST", body: form, signal: controller.signal,
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({})) as { detail?: string };
          throw new Error(detail.detail || `语音识别失败（${response.status}），请稍后重试。`);
        }
        const data = await response.json() as { text?: string };
        if (!current()) return;
        const text = (data.text || "").trim();
        if (text) onTranscriptRef.current(text);
        else setError("未识别到清晰语音，请靠近麦克风重新录制。");
      } catch (e) {
        if (current()) setError(controller.signal.aborted ? "语音识别超时，请缩短录音后重试。" :
          e instanceof Error ? e.message : "语音识别失败，请重试。");
      } finally {
        clearTimeout(timeout);
        if (current()) { busy.current = false; setState("idle"); }
      }
    };
    recorder.onerror = () => { recorder.onstop = null; release(); if (recorder.state !== "inactive") recorder.stop(); fail("录音中断，请重新录制。"); };
    recorder.start();
    timerRef.current = setTimeout(() => { if (recorder.state === "recording") recorder.stop(); }, MAX_RECORDING_MS);
    setState("recording");
  }, [release]);

  const toggle = useCallback(() => { if (state === "recording") stop(); else if (state === "idle") void start(); }, [start, state, stop]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current++;
      requestRef.current?.abort();
      const recorder = recorderRef.current;
      if (recorder) { recorder.onstop = null; if (recorder.state !== "inactive") recorder.stop(); }
      release();
    };
  }, [release]);
  return { state, error, toggle, start, stop };
}
