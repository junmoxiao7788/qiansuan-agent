import { DEFAULT_REASONING_EFFORT, isReasoningEffort, type ReasoningEffort } from '@hermes/shared'

import { normalize } from '@/lib/text'

/** Compact labels for chrome where space is tight (pill, picker rows). Menus
 *  and settings use the translated `shell.modelOptions` strings instead. */
// 黔算智能体：这些短标签会直接印在输入框右下角的 pill 上（"推理强度: Med"），
// 所以跟着界面语言走，不再保留上游英文缩写。
const SHORT_LABELS: Record<string, string> = {
  none: '关闭',
  minimal: '最少',
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '较高',
  max: '最高',
  ultra: '极强'
}

/**
 * A pick the route does not send verbatim: `ultra` is a Hermes-internal step
 * that routes clamp to their strongest level (`max` on OpenAI-compatible wires; the Codex
 * app-server sends it verbatim as codex's own mode), and the
 * CLI's `/reasoning` says so ("ultra (sends max on this route)"). The wire
 * level comes from the gateway's `session.info.reasoning_effort_wire`; nothing
 * is inferred client-side, so an unknown ('' — not yet stamped, or an
 * optimistic pick) or verbatim wire reads as "no clamp".
 */
export function reasoningEffortClamp(
  effort: string,
  wire: string | undefined
): { effort: ReasoningEffort; wire: ReasoningEffort } | null {
  const picked = normalize(effort)
  const sent = normalize(wire ?? '')

  if (!sent || sent === picked || !isReasoningEffort(picked) || !isReasoningEffort(sent)) {
    return null
  }

  return { effort: picked, wire: sent }
}

/** Compact label; a clamped pick shows both ends ("Ultra→Max") so the pill
 *  never presents a Hermes step as a wire level the route does not have. */
export function reasoningEffortLabel(effort: string, wire?: string): string {
  const key = normalize(effort)
  const clamp = reasoningEffortClamp(effort, wire)

  if (clamp) {
    return `${SHORT_LABELS[clamp.effort]}→${SHORT_LABELS[clamp.wire]}`
  }

  return key ? (SHORT_LABELS[key] ?? effort) : ''
}

/** Thinking is on unless a level explicitly says otherwise; an empty value
 *  means "inherit", so it resolves through `fallback` first. */
export const isThinkingEnabled = (effort: string, fallback: string = DEFAULT_REASONING_EFFORT): boolean =>
  normalize(effort || fallback) !== 'none'

/** The level a scale control should show. Empty inherits `fallback`; `none`
 *  (thinking off) selects nothing; anything unrecognized clamps to the default. */
export function resolveReasoningEffort(effort: string, fallback: string = DEFAULT_REASONING_EFFORT): string {
  const value = normalize(effort || fallback)

  if (value === 'none') {
    return ''
  }

  return isReasoningEffort(value) ? value : DEFAULT_REASONING_EFFORT
}
