/** One visible-tab stream with a non-overlapping lifecycle and polling fallback. */
export function startVisibleEventStream({
  url,
  refresh,
  onMessage,
  interval,
  enabled = true,
}: {
  url: string
  refresh: () => void
  onMessage: (event: MessageEvent) => void
  interval: number
  enabled?: boolean
}): () => void {
  if (typeof window === 'undefined') return () => {}
  let stopped = false
  let source: EventSource | undefined
  let fallback: ReturnType<typeof setInterval> | undefined
  function clearFallback() {
    clearInterval(fallback)
    fallback = undefined
  }
  function poll() {
    if (!stopped && !document.hidden) refresh()
  }
  function startFallback() {
    if (!stopped && !document.hidden && enabled) fallback ??= setInterval(poll, interval)
  }
  function disconnect() {
    const previous = source
    source = undefined
    clearFallback()
    if (previous) {
      previous.onmessage = previous.onerror = previous.onopen = null
      previous.close()
    }
  }
  function connect() {
    if (stopped || document.hidden || source || !enabled) return
    if (typeof EventSource === 'undefined') {
      startFallback()
      return
    }
    try {
      const current = new EventSource(url, { withCredentials: true })
      source = current
      const live = () => !stopped && source === current && !document.hidden
      current.onmessage = event => {
        if (live()) onMessage(event)
      }
      current.onerror = () => {
        if (live()) startFallback()
      }
      current.onopen = () => {
        if (live()) clearFallback()
      }
    } catch {
      startFallback()
    }
  }
  function visible() {
    if (document.hidden) disconnect()
    else {
      connect()
      poll()
    }
  }
  function focus() {
    if (!document.hidden) {
      connect()
      poll()
    }
  }
  document.addEventListener('visibilitychange', visible)
  window.addEventListener('focus', focus)
  visible()
  return () => {
    if (stopped) return
    stopped = true
    disconnect()
    document.removeEventListener('visibilitychange', visible)
    window.removeEventListener('focus', focus)
  }
}
