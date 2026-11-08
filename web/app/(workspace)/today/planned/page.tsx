"use client";

import Link from "next/link";
import { BookOpen, ClipboardCheck, Compass, MessageCircle, Presentation, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LearningShell } from "@/components/learning/LearningShell";
import { scopedUrl } from "@/lib/workspace-scope";

const features = [
  { key: "workflow", icon: Compass },
  { key: "classroom", icon: Presentation },
  { key: "tutor", icon: MessageCircle },
  { key: "teach", icon: Sparkles },
  { key: "check", icon: ClipboardCheck },
] as const;

export default function PlannedFeaturesPage() {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith("zh");
  return (
    <LearningShell title={t("workbench.upcoming")} subtitle={t("workbench.upcomingIntro")} icon={BookOpen} back={false}
      action={<Link href={scopedUrl("/today")} className="text-sm font-medium text-primary underline">{t("workbench.overview")}</Link>}>
      <ul className="grid gap-4 sm:grid-cols-2">
        {features.map(({ key, icon: Icon }) => (
          <li key={key} className="rounded-xl border border-border bg-card p-5">
            <div className="flex items-center gap-3">
              <Icon size={20} className="text-primary" aria-hidden />
              <h2 className="flex-1 font-medium">{t(`workbench.${key}`)}</h2>
              <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">{key === "workflow" || key === "tutor" ? (zh ? "可使用" : "Available") : t("workbench.planned")}</span>
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{t(`workbench.${key}Detail`)}</p>
            {(key === "workflow" || key === "tutor") && <Link href={scopedUrl("/learning")} className="mt-4 inline-block text-sm text-primary underline">{zh ? (key === "tutor" ? "选择项目进入陪学" : "开始学习") : "Open learning"}</Link>}
          </li>
        ))}
      </ul>
    </LearningShell>
  );
}
