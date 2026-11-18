"use client";

import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { diagnosticOpeningScope } from "@/lib/diagnostic-impact";
import { consumePendingPrompt, setPendingPrompt } from "@/lib/pending-prompt";
import {
  MASTERY_OPENING_SCOPE,
  masteryOpeningMessage,
  type MasteryMode,
} from "@/lib/mastery-mode";

/** Keep the opening pending until the route's session can accept a turn. */
export function useMasteryOpening({
  pathId,
  topicReady,
  hasMessages,
  sessionLoading,
  sessionError,
  sessionMode,
  isStreaming,
  submit,
}: {
  pathId: string;
  topicReady: boolean;
  hasMessages: boolean;
  sessionLoading: boolean;
  sessionError: string | null;
  sessionMode: MasteryMode;
  isStreaming: boolean;
  submit: (content: string) => boolean;
}) {
  const { t } = useTranslation();
  const openingSentRef = useRef("");
  useEffect(() => {
    if (!topicReady || hasMessages || sessionLoading || sessionError)
      return;
    if (isStreaming || openingSentRef.current === pathId) return;
    const diagnostic = consumePendingPrompt(diagnosticOpeningScope(pathId)).trim();
    const opening = diagnostic ||
      consumePendingPrompt(MASTERY_OPENING_SCOPE).trim() ||
      masteryOpeningMessage(sessionMode, t);
    // Study mode has no automatic opening. Latch only after submitting.
    if (opening) {
      if (submit(opening)) openingSentRef.current = pathId;
      else setPendingPrompt(opening, diagnostic ? diagnosticOpeningScope(pathId) : MASTERY_OPENING_SCOPE);
    }
  }, [
    hasMessages,
    pathId,
    sessionError,
    sessionLoading,
    sessionMode,
    isStreaming,
    submit,
    t,
    topicReady,
  ]);
}
