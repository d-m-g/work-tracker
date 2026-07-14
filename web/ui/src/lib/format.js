/** Formatting helpers. Pure functions — no React, no fetch. */

/**
 * Render a duration as `H:MM:SS`, matching the CLI's output exactly.
 * Negative input is clamped, mirroring `utils.format_duration` in Python.
 */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(seconds ?? 0))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

/** Render a duration compactly (`7h 32m`) for at-a-glance summaries. */
export function formatCompact(seconds) {
  const total = Math.max(0, Math.floor(seconds ?? 0))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (hours === 0 && minutes === 0) return `${total}s`
  if (hours === 0) return `${minutes}m`
  return `${hours}h ${String(minutes).padStart(2, '0')}m`
}

/**
 * Render an ISO-8601 timestamp as a local clock time (`09:15`).
 *
 * The stored timestamps carry an explicit UTC offset, so `Date` reads them
 * unambiguously and renders them in the viewer's own timezone.
 */
export function formatTime(iso) {
  if (!iso) return '—'
  // 24-hour, always: this is an instrument, and "09:03 PM" reads like a receipt.
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** Render an ISO-8601 timestamp as a friendly date (`Tue 14 Jul`). */
export function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString([], {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}
