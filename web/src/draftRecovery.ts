import type { Anchor } from './commentAnchors'

export type DraftSnapshot = { title: string; body: string; notes: string; summary: string; sourceUrl: string; wechatTheme: string; reviewIssues: Anchor[] }
export type Recovery = { base: string; value: DraftSnapshot; at: number }
export const snapshotKey = (value: DraftSnapshot) => JSON.stringify(value)
export const recoveryKey = (id: number) => `cw-draft-recovery-v1:${id}`
export function readRecovery(id: number): Recovery | null {
  try {
    const r = JSON.parse(localStorage.getItem(recoveryKey(id)) || 'null')
    if (!r || typeof r.base !== 'string' || !r.value || !Array.isArray(r.value.reviewIssues)) return null
    for (const k of ['title','body','notes','summary','sourceUrl','wechatTheme']) if (typeof r.value[k] !== 'string') return null
    return r
  } catch { return null }
}
export function writeRecovery(id: number, base: string, value: DraftSnapshot): boolean {
  try { localStorage.setItem(recoveryKey(id), JSON.stringify({ base, value, at: Date.now() })); return true }
  catch { return false }
}
export function clearRecovery(id: number, value?: DraftSnapshot) {
  try { if (!value || snapshotKey(readRecovery(id)?.value || value) === snapshotKey(value)) localStorage.removeItem(recoveryKey(id)) } catch { /* Server save remains authoritative. */ }
}
