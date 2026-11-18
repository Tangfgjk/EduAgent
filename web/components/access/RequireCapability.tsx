"use client";

import { Lock } from "lucide-react";
import { useTranslation } from "react-i18next";

import { type Capability } from "@/lib/capability-routes";

import { useCapabilityAccess } from "./CapabilityAccessContext";

/**
 * Full-surface "this feature is locked" notice. Shown in place of a feature
 * page when the current user lacks the required model capability. Never hides
 * the feature — it explains why it is unavailable and what to do.
 */
export function LockedFeatureNotice({
  capability,
}: {
  capability: Capability;
}) {
  const { t } = useTranslation();
  void capability;

  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-center gap-4 rounded-2xl border border-[var(--border)] bg-[var(--secondary)]/40 px-8 py-10 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--background)] text-[var(--muted-foreground)]">
          <Lock size={20} strokeWidth={1.8} />
        </div>
        <h2 className="text-base font-semibold text-[var(--foreground)]">
          {t("Feature locked")}
        </h2>
        <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
          请先配置自己的提供方和 API Key，配置完成后即可使用陪学。

        </p>
        <a href="/settings/personal-models" className="rounded-xl bg-[var(--primary)] px-4 py-2 text-[var(--primary-foreground)]">配置我的模型</a>
      </div>
    </div>
  );
}

/**
 * Renders {children} only when the user has the required capability; otherwise
 * renders the locked notice. Pass capability=null to never gate.
 */
export function RequireCapability({
  capability,
  children,
}: {
  capability: Capability | null;
  children: React.ReactNode;
}) {
  const { has } = useCapabilityAccess();
  if (capability && !has(capability)) {
    return <LockedFeatureNotice capability={capability} />;
  }
  return <>{children}</>;
}
