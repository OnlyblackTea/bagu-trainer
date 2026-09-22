import type { InstanceOut, ReviewOut, Ticket, TrackDetail, TrackSummary } from '../types'

export const trackId = 'database-bagu'

/** 与 Plan 1 的 demo Track 同形：目录名描述式、身份另起、含一个只上榜不出卷的阶段。 */
export const track: TrackDetail = {
  id: trackId,
  name: '数据库八股实战',
  language: 'zh-CN',
  trust_level: 'self',
  stages: [
    {
      stage: 'stage-00-onboarding',
      kind: 'assessed',
      ticket_id: 'db-stage-00',
      stage_index: 0,
      title: '环境入职：把骨架跑起来',
      prerequisites: [],
    },
    {
      stage: 'stage-01-product-query-index',
      kind: 'assessed',
      ticket_id: 'db-stage-01',
      stage_index: 1,
      title: '商品搜索查询慢：索引与执行计划',
      prerequisites: ['db-stage-00'],
    },
    {
      stage: 'stage-03-deadlock-optional',
      kind: 'board_only',
      ticket_id: null,
      stage_index: null,
      title: null,
      prerequisites: [],
    },
  ],
}

export function makeInstance(over: Partial<InstanceOut> = {}): InstanceOut {
  return {
    id: 'ti-1',
    learner: 'deployer',
    track: trackId,
    ticket_id: 'db-stage-00',
    ticket_version: 1,
    engine_version: 'a-prime-0.1.0',
    test_version: 1,
    state: 'available',
    created_at: '2026-09-10T00:00:00Z',
    gate: '可开始：start 本实例，再 provision 拉取本地工作区。',
    ...over,
  }
}

/** `GET /tracks` 的一条（比 `TrackDetail` 少了逐阶段条目，多了两个计数）。 */
export const trackSummary: TrackSummary = {
  id: trackId,
  name: track.name,
  language: track.language,
  trust_level: track.trust_level,
  stages: track.stages.map((s) => s.stage),
  assessed_stages: 2,
  board_only_stages: 1,
}

export const ticket: Ticket = {
  id: 'db-stage-00',
  stage: 0,
  title: '环境入职：把骨架跑起来',
  ticket_version: 1,
  problem: '按工单完成本阶段任务（占位内容）。',
  concepts: ['最左前缀', '覆盖索引', 'EXPLAIN'],
  files_to_change: ['src/db-stage-00/Solution.java'],
  hints: [{ id: 'H1', text: '先看 EXPLAIN，再决定索引列序。' }],
  grader: {
    test_group: 'db-stage-00',
    canonical_test: 'tests/db-stage-00/AcceptanceTest.java',
    expect: '初始红 → 改对后绿',
  },
}

export const review: ReviewOut = {
  id: 'stub-rev-stub-sub-1',
  submission_id: 'sub-1',
  recommendation: 'request_changes',
  summary: '模拟评审结论（占位后端）。',
  comments: [
    {
      dimension: 'security',
      anchor: 'src/Foo.java#L354',
      text: '模拟意见：用户输入被拼进了 SQL（占位后端，非真实评审）。',
      path: 'src/Foo.java',
      line: 354,
      side: 'right',
      quote: 'String sql = "select";',
    },
  ],
  dimension_findings: [{ dimension: 'correctness', commentary: 'correctness：模拟评语。' }],
  truncated_files: [],
  created_at: '2026-09-10T00:00:00Z',
}
