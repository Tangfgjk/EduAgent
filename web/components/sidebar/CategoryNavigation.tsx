'use client'

import { useId, useState, startTransition, type ReactNode } from 'react'
import dynamic from 'next/dynamic'
import { usePathname, useRouter } from 'next/navigation'
import { m, useReducedMotion } from 'framer-motion'
import { MessageCircle, Search, PenLine, ClipboardCheck } from 'lucide-react'
import {
  HomeShelfIcon,
  LearningShelfIcon,
  ResourcesShelfIcon,
} from './ShelfIcons'
import { useTranslation } from 'react-i18next'
import Tooltip from '@/shared/ui/Tooltip'
import { categoryForPath, type NavigationCategory } from '@/lib/navigation'
import { navVisibleUnderPolicy } from './nav-entries'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { navigateTask } from '@/lib/workspace-scope'
import { NavigationRow, OverviewRow, Section } from './NavigationPrimitives'
import { SHORTCUTS } from '@/lib/shortcuts'
import type { LearningSessions } from './LearningNavigation'

// Feature libraries load when their category opens, not in every workspace.
const LearningNavigation = dynamic(() =>
  import('./LearningNavigation').then(module => module.LearningNavigation)
)
const ResourcesNavigation = dynamic(() =>
  import('./ResourcesNavigation').then(module => module.ResourcesNavigation)
)
export const CATEGORIES = [
  { id: 'home', label: 'navigation.home', icon: HomeShelfIcon, href: '/today' },
  { id: 'learning', label: 'navigation.learning', icon: LearningShelfIcon, href: '/learning' },
  { id: 'resources', label: 'navigation.resources', icon: ResourcesShelfIcon, href: '/space' },
] as const

export function useCategoryNavigation() {
  const pathname = usePathname()
  const { learningPolicy } = useAuthStatus()
  const categories = CATEGORIES.filter(item =>
    item.id === 'home' || navVisibleUnderPolicy(item.href, learningPolicy?.allowedSurfaces ?? null)
  )
  const [visited, setVisited] = useState<NavigationCategory[]>([])
  const [selection, setSelection] = useState<{
    pathname: string
    category: NavigationCategory
  } | null>(null)
  const desired = selection?.pathname === pathname ? selection.category : categoryForPath(pathname)
  const category = categories.find(item => item.id === desired)?.id ?? categories[0]?.id ?? 'home'
  return {
    category,
    categories,
    visited,
    select: (next: NavigationCategory) => {
      startTransition(() => {
        setVisited(previous => [...new Set([...previous, category, next])])
        setSelection({ pathname, category: next })
      })
    },
    allowedSurfaces: learningPolicy?.allowedSurfaces ?? null,
  }
}
type Navigation = ReturnType<typeof useCategoryNavigation>

export function CategoryTabs({
  nav,
  collapsed,
  onSearch,
}: {
  nav: Navigation
  collapsed: boolean
  /** The rail keeps search with its tabs; the open sidebar has it in its header. */
  onSearch?: () => void
}) {
  const { t } = useTranslation()
  const router = useRouter()
  const id = useId()
  const reduced = useReducedMotion()
  const select = (category: (typeof CATEGORIES)[number]) => {
    nav.select(category.id)
    navigateTask(category.href, router.push)
  }
  const tab = (item: (typeof CATEGORIES)[number]) => {
    const index = nav.categories.indexOf(item)
    const active = nav.category === item.id
    return (
      <Tooltip
        key={item.id}
        label={t(item.label)}
        side={collapsed ? 'right' : 'bottom'}
        shortcut={SHORTCUTS.category(index)}
        suppressed={!collapsed && active}
      >
        <button
          id={`category-${item.id}`}
          type="button"
          role="tab"
          aria-controls="category-content"
          aria-label={t(item.label)}
          aria-selected={active}
          tabIndex={active ? 0 : -1}
          onKeyDown={event => {
            const delta = ['ArrowRight', 'ArrowDown'].includes(event.key)
              ? 1
              : ['ArrowLeft', 'ArrowUp'].includes(event.key)
                ? -1
                : 0
            if (!delta && !['Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? nav.categories.length - 1
                  : (index + delta + nav.categories.length) % nav.categories.length
            select(nav.categories[next])
            event.currentTarget
              .closest('[role="tablist"]')
              ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
              [next]?.focus()
          }}
          onClick={() => select(item)}
          className={active ? 'is-active' : ''}
        >
          {active && (
            <m.span
              layoutId={`category-pill-${id}`}
              className="dt-category-pill"
              transition={{ duration: reduced ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
            />
          )}
          <item.icon size={20} strokeWidth={1.6} />
          <span>{t(item.label)}</span>
        </button>
      </Tooltip>
    )
  }
  return (
    <div className={`dt-category-bar ${collapsed ? 'is-collapsed' : ''}`}>
      <div
        role="tablist"
        aria-label={t('navigation.categories')}
        aria-orientation={collapsed ? 'vertical' : 'horizontal'}
      >
        {nav.categories.map(tab)}
      </div>
      {onSearch && (
        <Tooltip label={t('navigation.search')} side={collapsed ? 'right' : 'bottom'} shortcut={SHORTCUTS.search}>
          <button type="button" className="dt-nav-icon" aria-label={t('navigation.search')} aria-haspopup="dialog" onClick={onSearch}>
            <Search size={16} strokeWidth={1.6} />
          </button>
        </Tooltip>
      )}
    </div>
  )
}

export function CategoryBody({
  nav,
  collapsed,
  sessions,
  learningSessions,
}: {
  nav: Navigation
  collapsed: boolean
  /** The home shelf's conversation records. */
  sessions: ReactNode
  /** Learning conversations, filed by the learning shelf under their records. */
  learningSessions?: LearningSessions
}) {
  const { t } = useTranslation()
  if (collapsed) return null
  return (
    <div
      id="category-content"
      role="tabpanel"
      aria-labelledby={`category-${nav.category}`}
      className={`dt-category-content ${nav.category === 'learning' ? 'dt-category-content-fixed' : ''}`}
    >
      {nav.category === 'home' && (
        <>
          <OverviewRow href="/today" label={t('workbench.overview')} icon={HomeShelfIcon} />
          {navVisibleUnderPolicy('/chat', nav.allowedSurfaces) && (
            <OverviewRow href="/chat" label={t('navigation.chat')} icon={MessageCircle} />
          )}
          <NavigationRow href="/today/planned" label={t('workbench.upcoming')} icon={ClipboardCheck} />
          <Section title={t('navigation.workspace')}>
            {[
              { href: '/co-writer', label: 'Co-Writer', icon: PenLine },
            ]
              .filter(item => navVisibleUnderPolicy(item.href, nav.allowedSurfaces))
              .map(item => (
                <NavigationRow
                  key={item.href}
                  href={item.href}
                  label={t(item.label)}
                  icon={item.icon}
                />
              ))}
          </Section>
          {sessions}
        </>
      )}
      {(nav.category === 'learning' || nav.visited.includes('learning')) && (
        <div hidden={nav.category !== 'learning'} className="dt-category-panel">
          <LearningNavigation active={nav.category === 'learning'} sessions={learningSessions} />
        </div>
      )}
      {(nav.category === 'resources' || nav.visited.includes('resources')) && (
        <div hidden={nav.category !== 'resources'}>
          <ResourcesNavigation />
        </div>
      )}
    </div>
  )
}
