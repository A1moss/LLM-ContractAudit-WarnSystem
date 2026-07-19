/**
 * Parse a DB timestamp as UTC and format for display.
 *
 * SQLite CURRENT_TIMESTAMP stores UTC. The backend returns naive ISO
 * strings (no timezone), so new Date() treats them as local time and
 * the display is off by the UTC offset (e.g. +8h in China).
 *
 * This function appends "Z" before parsing so the browser interprets
 * the value as UTC and converts to the user's local timezone.
 */
export function formatTime(ts) {
  if (!ts) return '—'

  // If already has a timezone marker, parse as-is
  let s = String(ts)
  if (!/[Zz+\-]\d{2}:\d{2}$/.test(s) && !s.endsWith('Z') && !s.endsWith('z')) {
    s += 'Z'
  }

  const d = new Date(s)
  if (isNaN(d.getTime())) return ts // unparseable → return raw

  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
