import { beforeEach, afterEach, it, expect, vi } from 'vitest'
import { startVisibleEventStream } from '@/lib/visible-event-stream'
import { startCalendarSync } from '@/features/calendar/store/calendar-store'
import { startTaskBoardSync } from '@/lib/task-board-store'
const m = vi.hoisted(() => ({ board: vi.fn(), calendars: vi.fn() }))
vi.mock('@/lib/task-board-api', () => ({
  getTaskBoard: m.board,
  normalizeTaskBoard: (x: unknown) => x,
  subscribeTaskBoardChanges: () => () => {},
  taskBoardUrl: () => '/task-events',
}))
vi.mock('@/features/calendar/transport/api', () => ({
  calendarStreamUrl: () => '/calendar-events',
  listCalendars: m.calendars,
  getLayers: vi.fn(),
  getTaskBlocks: vi.fn(),
  listOccurrences: vi.fn(),
}))
class Source {
  static instances: Source[] = []
  close = vi.fn()
  onmessage: unknown
  onerror: unknown
  onopen: unknown
  constructor() {
    Source.instances.push(this)
  }
}
let hidden = false
let cleanup: (() => void)[] = []
beforeEach(() => {
  hidden = false
  Source.instances = []
  cleanup = []
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
  vi.stubGlobal('EventSource', Source)
  m.board.mockResolvedValue({ revision: 0, cards: [] })
  m.calendars.mockResolvedValue({ calendars: [], revision: 0 })
})
afterEach(() => {
  cleanup.reverse().forEach(stop => stop())
  vi.unstubAllGlobals()
})
for (const [name, start] of [
  ['calendar', startCalendarSync],
  ['board', startTaskBoardSync],
] as const) {
  it(name + ' releases the stream in a hidden tab and reconnects exactly once when visible', () => {
    cleanup.push(start())
    cleanup.push(start())
    expect(Source.instances).toHaveLength(1)
    hidden = true
    document.dispatchEvent(new Event('visibilitychange'))
    expect(Source.instances[0].close).toHaveBeenCalledOnce()
    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    document.dispatchEvent(new Event('visibilitychange'))
    expect(Source.instances).toHaveLength(2)
  })
  it(name + ' does not open a connection until a hidden tab is shown', () => {
    hidden = true
    cleanup.push(start())
    expect(Source.instances).toHaveLength(0)
    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    expect(Source.instances).toHaveLength(1)
  })
}

it('ignores messages from a disconnected stream and stops fallback polling while hidden',()=>{
 vi.useFakeTimers();
 const refresh=vi.fn(),message=vi.fn();
 const stop=startVisibleEventStream({url:'/events',refresh,onMessage:message,interval:3000});
 cleanup.push(stop);
 const source=Source.instances[0];
 const late=source.onmessage as (event:MessageEvent)=>void;
 (source.onerror as ()=>void)();
 vi.advanceTimersByTime(3000);
 expect(refresh).toHaveBeenCalledTimes(2);
 hidden=true;document.dispatchEvent(new Event('visibilitychange'));
 late(new MessageEvent('message',{data:'stale'}));
 vi.advanceTimersByTime(9000);
 expect(message).not.toHaveBeenCalled();expect(refresh).toHaveBeenCalledTimes(2);
 stop();stop();hidden=false;document.dispatchEvent(new Event('visibilitychange'));
 expect(Source.instances).toHaveLength(1);
 vi.useRealTimers();
});
