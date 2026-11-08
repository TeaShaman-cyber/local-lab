/**
 * Alerts chip + pane.
 *
 * Data comes from this plugin's own backend (dashboard/plugin_api.py), never
 * from a renderer fetch: Grafana sends no CORS headers and the token stays on
 * the server. Everything here is presentation.
 *
 * One live rule: a green dot must mean "the stack answered and nothing is
 * firing". No token, no backend and an unreachable stack are all grey, because
 * a calm green light over an unknown state is the failure this widget exists to
 * prevent.
 */

import { Badge, StatusDot, host, useQuery } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const QUERY_KEY = ['local-alerts', 'state']
const REFETCH_MS = 30_000

const PANE = { placement: 'bottom', height: '260px' }

function tone(state) {
  if (state === 'firing') return 'bad'
  if (state === 'pending' || state === 'nodata') return 'warn'
  if (state === 'normal') return 'good'
  return 'muted'
}

function age(iso) {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  if (!Number.isFinite(ms) || ms < 0) return ''
  const min = Math.round(ms / 60_000)
  if (min < 1) return 'только что'
  if (min < 60) return `${min} мин`
  const hours = Math.round(min / 60)
  if (hours < 24) return `${hours} ч`
  return `${Math.round(hours / 24)} д`
}

// ctx.rest is namespaced per plugin and only exists inside register(); keep the
// callable handy so the hook body stays a plain query.
let restAlerts = () => Promise.reject(new Error('backend not bound'))

function useAlerts() {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => restAlerts('/alerts'),
    refetchInterval: REFETCH_MS,
    staleTime: 15_000,
    retry: 1
  })
}

function headline(data) {
  if (!data) return { state: 'unknown', text: '…' }
  if (data.status === 'no_token') return { state: 'no_token', text: 'нет доступа' }
  if (data.status === 'error') return { state: 'error', text: data.reason || 'ошибка' }
  const firing = data.counts?.firing ?? 0
  const pending = data.counts?.pending ?? 0
  const unhealthy = data.counts?.nodata ?? 0
  if (firing > 0) return { state: 'firing', text: String(firing) }
  if (pending > 0) return { state: 'pending', text: String(pending) }
  // Quiet is not the same as healthy: a rule whose query fails or returns no
  // data is inactive too, and painting that green is how a broken monitor
  // looks like an all-clear.
  if (unhealthy > 0) return { state: 'nodata', text: 'нет данных' }
  return { state: 'normal', text: 'ok' }
}

function AlertChip() {
  const { data, isError } = useAlerts()
  const head = isError ? { state: 'error', text: 'нет связи' } : headline(data)
  const dot = tone(head.state)

  return jsxs('span', {
    className: 'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-secondary)',
    title: 'Grafana: состояние правил',
    children: [jsx(StatusDot, { tone: dot }), jsx('span', { children: `Alerts ${head.text}` })]
  })
}

function Row({ rule }) {
  const open = () => {
    if (rule.url) host.os?.openExternal?.(rule.url)
  }

  return jsxs('div', {
    className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) py-1.5 last:border-0',
    children: [
      jsx(StatusDot, { tone: tone(rule.state) }),
      jsxs('span', {
        className: 'min-w-0 flex-1',
        children: [
          jsx('span', { className: 'block truncate text-(--ui-text-secondary)', children: rule.name }),
          rule.summary
            ? jsx('span', {
                className: 'block truncate text-[0.6875rem] text-(--ui-text-quaternary)',
                children: rule.summary
              })
            : null
        ]
      }),
      rule.severity ? jsx(Badge, { variant: 'outline', children: rule.severity }) : null,
      rule.health === 'nodata' || rule.health === 'error'
        ? jsx(Badge, { variant: 'secondary', children: rule.health })
        : null,
      jsx('span', {
        className: 'shrink-0 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: age(rule.active_at || rule.last_evaluated)
      }),
      rule.url
        ? jsx('button', {
            className: 'shrink-0 text-[0.6875rem] text-(--ui-accent)',
            onClick: open,
            type: 'button',
            children: 'Grafana'
          })
        : null
    ]
  })
}

function AlertsPane() {
  const { data, isError, error, isLoading } = useAlerts()

  if (isLoading) {
    return jsx('div', { className: 'p-3 text-(--ui-text-tertiary)', children: 'проверяю…' })
  }
  if (isError) {
    return jsx('div', {
      className: 'p-3 text-(--ui-text-tertiary)',
      children: `Бэкенд плагина недоступен: ${error?.message || 'ошибка'}. Проверь plugins.enabled и рестарт гейтвея.`
    })
  }
  if (data?.status === 'no_token') {
    return jsx('div', {
      className: 'p-3 text-(--ui-text-tertiary)',
      children: 'Нет токена: задай GRAFANA_ALERTS_TOKEN (service account, роль Viewer) и перезапусти гейтвей.'
    })
  }
  if (data?.status === 'error') {
    return jsxs('div', {
      className: 'p-3 text-(--ui-text-tertiary)',
      children: [
        jsx('div', { children: `Grafana не отдала данные: ${data.reason}` }),
        jsx('div', { className: 'text-[0.6875rem]', children: data.grafana })
      ]
    })
  }

  const rules = data?.rules || []
  if (!rules.length) {
    return jsx('div', { className: 'p-3 text-(--ui-text-tertiary)', children: 'Правил нет.' })
  }

  return jsxs('div', {
    className: 'h-full overflow-auto px-3',
    children: [
      jsx('div', {
        className: 'py-1.5 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: `обновлено ${age(data.fetched_at) || 'сейчас'} · firing ${data.counts.firing} · pending ${data.counts.pending}`
      }),
      ...rules.map(rule => jsx(Row, { rule }, `${rule.folder}/${rule.name}`))
    ]
  })
}

export default {
  id: 'local-alerts',
  name: 'Alerts',
  register(ctx) {
    restAlerts = path => ctx.rest(path)

    ctx.register({ id: 'chip', area: 'statusBar.right', order: 120, render: () => jsx(AlertChip, {}) })
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Alerts',
      order: 60,
      data: PANE,
      render: () => jsx(AlertsPane, {})
    })
  }
}
