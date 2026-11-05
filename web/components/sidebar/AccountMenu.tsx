'use client'

import Link from 'next/link'
import { useId, useState, type ReactNode } from 'react'
import { ChevronUp, Settings, UserRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { useMenuPopover } from '@/shared/ui/useMenuPopover'

export function AccountMenu({
  compact = false,
  accountSlot,
  onNavigate,
}: {
  compact?: boolean
  accountSlot?: ReactNode
  onNavigate?: () => void
}) {
  const { t } = useTranslation()
  const auth = useAuthStatus()
  const [open, setOpen] = useState(false)
  const root = useMenuPopover<HTMLDivElement>(open, setOpen)
  const id = useId()
  const label = auth.username || t(auth.authenticated ? 'wenjin.account' : 'wenjin.localUser')
  const close = () => {
    setOpen(false)
    onNavigate?.()
  }
  return (
    <div ref={root} className={`wenjin-account-menu ${compact ? "is-compact" : ""}`}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        aria-label={t('wenjin.accountMenu')}
        onClick={() => setOpen(value => !value)}
        className="wenjin-account-trigger"
      >
        <span className="wenjin-account-avatar"><UserRound size={17} /></span>
        {!compact && <>
          <span className="min-w-0 flex-1 text-left">
            <span className="block truncate text-sm font-medium">{label}</span>
            <span className="block text-xs text-muted-foreground">{t('wenjin.accountSettings')}</span>
          </span>
          <ChevronUp size={14} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
        </>}
      </button>
      {open && (
        <div id={id} role="menu" aria-label={t('wenjin.accountMenu')} className="dt-popover wenjin-account-panel">
          <div className="wenjin-account-summary">
            <span className="wenjin-account-avatar"><UserRound size={18} /></span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{label}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t(auth.isAdmin ? 'wenjin.administrator' : auth.authenticated ? 'wenjin.studentAccount' : 'wenjin.localUser')}
              </p>
            </div>
          </div>
          <div className="dt-menu-divider" />
          <Link role="menuitem" href="/settings" prefetch={false} onClick={close} className="dt-menu-item">
            <Settings size={16} /><span>{t('Settings')}</span>
          </Link>
          {accountSlot && <div className="dt-menu-slot">{accountSlot}</div>}
        </div>
      )}
    </div>
  )
}
