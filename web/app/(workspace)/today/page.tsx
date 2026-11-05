"use client";

import Link from "next/link";
import { ArrowRight, BookOpen, ClipboardCheck, Compass, Library, Plus, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LearningShell } from "@/components/learning/LearningShell";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { useLearningIndex } from "@/hooks/useLearningIndex";
import { useModules } from "@/hooks/useModules";
import { allLearningActivity } from "@/lib/learning-dashboard";
import { scopedUrl } from "@/lib/workspace-scope";

const actionClass = "inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground focus-visible:outline-2 focus-visible:outline-ring";
const cardClass = "rounded-[var(--wenjin-desk-radius)] border border-border bg-card p-5";

function DeskIllustration() {
  return (
    <svg className="wenjin-hero-illustration" viewBox="0 0 160 118" fill="none" aria-hidden="true">
      <path d="M12 100h136M37 110V13M43 26l97 62M43 86l97-55" stroke="currentColor" strokeWidth="1.3" />
      <path d="M37 56h51v44" stroke="currentColor" strokeDasharray="3 4" />
      <circle cx="88" cy="56" r="17" stroke="currentColor" strokeDasharray="2 4" />
      <circle cx="88" cy="56" r="5" fill="var(--wenjin-gold)" />
      <path d="M125 16v12m-6-6h12" stroke="var(--wenjin-gold)" strokeWidth="1.4" />
    </svg>
  );
}

export default function TodayPage() {
  const { t } = useTranslation();
  const auth = useAuthStatus();
  const modules = useModules();
  const { sources, loading, failed, retry } = useLearningIndex(!auth.loading);
  const allowed = auth.learningPolicy?.allowedSurfaces;
  const canUse = (href: string, surface: string) =>
    modules.available(href) && (!allowed || allowed.includes(surface));
  const activities = allLearningActivity(sources);
  const latest = activities.find((activity) => activity.progress === undefined || activity.progress < 1);
  const startLinks = [
    { href: "/learning/reading", surface: "reading", label: "workbench.upload", icon: Upload },
    { href: "/learning/mastery", surface: "chat", label: "workbench.topic", icon: BookOpen },
  ].filter(({ href, surface }) => canUse(href, surface));
  const choices = startLinks.length > 0 && (
    <div className="grid gap-3 sm:grid-cols-2">
      {startLinks.map(({ href, label, icon: Icon }) => (
        <Link key={href} href={scopedUrl(href + "?create=1")} className="wenjin-quick-link">
          <span className="wenjin-quick-icon"><Icon size={17} /></span>
          <span className="flex-1">{t(label)}</span><ArrowRight size={16} className="shrink-0 text-primary" />
        </Link>
      ))}
    </div>
  );

  return (
    <LearningShell
      title={t("workbench.title")} subtitle={t("workbench.subtitle")} icon={Compass} back={false} className="wenjin-today"
      action={canUse("/learning", "chat") && (
        <Link href={scopedUrl("/learning/new")} className={actionClass}><Plus size={16} />{t("navigation.createLearning")}</Link>
      )}
    >
      <div className="wenjin-today-heading">
        <span className="wenjin-eyebrow">桂子问津</span>
        <h2>{t("workbench.title")}</h2>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">{t("workbench.subtitle")}</p>
      </div>
      <div className="wenjin-today-grid">
        <div className="min-w-0 space-y-5">
          <section className="wenjin-hero" aria-busy={loading}>
            <DeskIllustration />
            {latest ? (
              <>
                <p className="wenjin-eyebrow">{t("workbench.resume")}</p>
                <h2 className="mb-4 max-w-[80%] break-words text-2xl font-semibold">{latest.title}</h2>
                <Link href={latest.href} className={actionClass}>{t("workbench.resume")}<ArrowRight size={16} /></Link>
              </>
            ) : (
              <>
                <h2 className="max-w-[80%] text-xl font-semibold">
                  {t(loading ? "Loading..." : failed.length ? "workbench.unavailable" : "workbench.empty")}
                </h2>
                {!loading && !failed.length && <p className="mt-2 max-w-[80%] text-sm text-muted-foreground">{t("workbench.emptyHint")}</p>}
                {!loading && failed.length > 0 && (
                  <button type="button" className="mt-3 text-sm text-primary underline" onClick={retry}>{t("Retry")}</button>
                )}
                <div className="relative mt-5">{choices}</div>
              </>
            )}
          </section>
          {latest && choices && (
            <section className={cardClass}>
              <h2 className="mb-3 text-lg font-semibold">{t("navigation.createLearning")}</h2>
              {choices}
            </section>
          )}
          {activities.length > 0 && (
            <section className={cardClass}>
              <h2 className="mb-3 text-lg font-semibold">{t("workbench.recent")}</h2>
              <div className="grid gap-2">
                {activities.slice(0, 4).map((activity) => (
                  <Link key={activity.key} href={activity.href} className="wenjin-quick-link">
                    <span className="wenjin-quick-icon"><BookOpen size={17} /></span>
                    <span className="min-w-0 flex-1 truncate">{activity.title}</span><ArrowRight size={16} />
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>
        <aside className={cardClass}>
          <h2 className="mb-3 text-sm font-semibold">{t("navigation.workspace")}</h2>
          <div className="space-y-2">
            {canUse("/learning", "chat") && <Link href={scopedUrl("/learning")} className="wenjin-quick-link"><BookOpen size={17} />{t("workbench.all")}</Link>}
            {!allowed && <Link href={scopedUrl("/space")} className="wenjin-quick-link"><Library size={17} />{t("navigation.allResources")}</Link>}
            <Link href={scopedUrl("/today/planned")} className="wenjin-quick-link">
              <ClipboardCheck size={17} /><span className="flex-1">{t("workbench.upcoming")}</span><span className="wenjin-planned">{t("workbench.planned")}</span>
            </Link>
          </div>
        </aside>
      </div>
    </LearningShell>
  );
}
