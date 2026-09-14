/**
 * 「模拟」徽标（spec §C.6 诚实占位 UI）。
 *
 * 凡桩产出的内容——阅卷报告、AI 评审、PR URL——都必须带这个徽标，并且悬浮说明
 * "当前是占位后端、真实实现会在未来切片接入"。它是学习者判断"这个结论能不能当真"
 * 的唯一线索，所以不做成可选装饰。
 */
export function SimulatedBadge({ what }: { what?: string }) {
  const title = what
    ? `${what}当前由占位后端产出，不是真实结果；真实实现会在未来切片接入。`
    : '当前由占位后端产出，不是真实结果；真实实现会在未来切片接入。'
  return (
    <span className="badge badge--simulated" data-testid="simulated-badge" title={title}>
      模拟{what ? ` · ${what}` : ''}
    </span>
  )
}
