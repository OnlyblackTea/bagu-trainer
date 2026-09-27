import { expect, test } from '@playwright/test'

/**
 * 学习者闭环，真后端 + 真浏览器。
 *
 * 串行执行：两条 decide 分支需要同一份递增的进度（stage-00 通过后 stage-01 才解锁），
 * 所以用 `describe.serial` 把这个依赖写成显式契约，而不是靠用例顺序的巧合。
 */
test.describe.serial('学习者闭环（真后端 + 真浏览器）', () => {
  test('pass 分支：发卷 → 开始 → 工作区 → diff → 提交 → 评审 → 决策解锁下一阶段', async ({
    page,
  }) => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '八股实战演练所' })).toBeVisible()

    // 诚实占位 UI：首页就要说明三处都是占位后端
    await expect(page.getByTestId('simulated-badge').first()).toBeVisible()

    // 入学（主页的入口；题目列表里的「未入学」徽标也会命中 /入学/，所以按 testid 点）
    await page.getByTestId('enroll').click()
    const stage00 = page.getByTestId('stage-stage-00-onboarding')
    await expect(stage00).toContainText('可开始')
    // 后继阶段保持未解锁 —— 前置门真的在管事
    await expect(page.getByTestId('stage-stage-01-product-query-index')).toContainText('未解锁')

    // 发卷（屏幕 2）：只给题面，不泄露解法
    await stage00.click()
    const ticket = page.getByTestId('ticket-view')
    await expect(ticket).toContainText('环境入职')
    await expect(ticket).toContainText('src/db-stage-00/Solution.java')
    await expect(ticket).not.toContainText('-sol')
    await expect(ticket).not.toContainText('origin/')

    // 开始 → 开发中
    await page.getByRole('button', { name: '开始本阶段' }).click()
    await expect(stage00).toContainText('开发中')

    // 工作区（屏幕 3）：当前阶段 + provision + 真本地 git diff
    const workspace = page.getByTestId('workspace-view')
    await expect(workspace.getByTestId('gate')).toContainText('当前阶段')
    await expect(workspace).toContainText('不替你跑 git 写操作')

    // 低频/有副作用的动作收在「更多操作」折叠里，先展开再用
    await workspace.getByText('更多操作').click()
    await page.getByRole('button', { name: '拉取本地工作区' }).click()
    const wsInfo = page.getByTestId('workspace-info')
    await expect(wsInfo).toBeVisible()
    await expect(wsInfo).toContainText('origin/stage-00-onboarding')
    await expect(wsInfo).toContainText('模拟')

    await page.getByRole('button', { name: '查看 diff' }).click()
    const diffInfo = page.getByTestId('diff-info')
    await expect(diffInfo).toContainText('diff --git')
    await expect(diffInfo).toContainText('模拟')

    // 提交 / 评审（屏幕 4）：两套 fixture 收在「高级调试选项」里，两条分支仍都能演示
    await page.getByTestId('advanced-debug').getByText('高级调试选项').click()
    await page.getByRole('button', { name: /提交答案/ }).click()
    await expect(page.getByTestId('submission-info')).toContainText('第 1 次')

    // 提交是**异步**的（202 + 轮询进度），报告随后渲染出来（屏幕 4a）
    const grading = page.getByTestId('grading-view')
    const progress = grading.getByTestId('grading-progress')
    await expect(progress).toContainText('重置环境')
    await expect(progress).toContainText('跑验收测试')
    await expect(progress).toContainText('解析结果')
    // 报告在 `grading-view` 里；历史面板里也各有一份（展开才看），所以必须限定范围。
    const report = grading.getByTestId('grading-report')
    await expect(report).toContainText('阅卷报告')
    await expect(report).toContainText('不可信自报告')
    await expect(report).toContainText('绿')
    await expect(report.getByTestId('simulated-badge')).toBeVisible() // 桩产出 ⇒ 必须标模拟

    // 历史：这次提交留在记录里（与进度同一份落库数据），展开能看到它的报告
    const history = page.getByTestId('history-view')
    await expect(history).toContainText('第 1 次')
    await expect(history).toContainText('阅卷 done')

    await page.getByTestId('review-scenario').selectOption('approve')
    await page.getByRole('button', { name: /发起 AI 评审/ }).click()
    const reviewInfo = page.getByTestId('review-info')
    await expect(reviewInfo).toContainText('模拟')
    await expect(reviewInfo).toContainText('approve')

    // 决策（屏幕 5）：确定性门 → pass ⇒ 解锁
    await page.getByRole('button', { name: /取确定性结论/ }).click()
    const verdict = page.getByTestId('verdict')
    await expect(verdict).toContainText('通过：下一阶段已解锁')
    await expect(verdict).toContainText('非 LLM 打分')
    // 闭环的出口就在判决里：不用回左栏找下一题
    await expect(page.getByTestId('go-next')).toBeVisible()

    await expect(stage00).toContainText('已通过')
    await expect(page.getByTestId('stage-stage-01-product-query-index')).toContainText('可开始')
  })

  test('fail 分支：评审 request_changes → 决策 fail → retry 回到开发中', async ({ page }) => {
    await page.goto('/')

    // 上一用例已解锁 stage-01，这里用它演示另一条分支
    const stage01 = page.getByTestId('stage-stage-01-product-query-index')
    await expect(stage01).toContainText('可开始')
    await stage01.click()

    await page.getByRole('button', { name: '开始本阶段' }).click()
    await expect(stage01).toContainText('开发中')

    // 自动测试给绿、评审要求改：门仍然 fail（评审是证据之一，不是分数）
    await page.getByTestId('advanced-debug').getByText('高级调试选项').click()
    await page.getByTestId('grading-scenario').selectOption('pass')
    await page.getByRole('button', { name: /提交答案/ }).click()
    await page.getByTestId('review-scenario').selectOption('request_changes')
    await page.getByRole('button', { name: /发起 AI 评审/ }).click()

    const reviewInfo = page.getByTestId('review-info')
    await expect(reviewInfo).toContainText('request_changes')
    // 行级锚定评论要显示出来
    await expect(reviewInfo).toContainText('src/Foo.java#L354')

    await page.getByRole('button', { name: /取确定性结论/ }).click()
    await expect(page.getByTestId('verdict')).toContainText('未通过：请按评审意见返工')
    await expect(stage01).toContainText('未通过')

    // 返回工作区修改 → 回到开发中，且旧结论被清空
    await page.getByTestId('retry').click()
    await expect(stage01).toContainText('开发中')
    await expect(page.getByTestId('verdict')).toHaveCount(0)
    await expect(page.getByTestId('submission-info')).toHaveCount(0)
  })
})

test('board-only 阶段：只上榜不出卷，点它不会进发卷', async ({ page }) => {
  await page.goto('/')

  const boardOnly = page.getByTestId('stage-stage-03-deadlock-optional')
  await expect(boardOnly).toContainText('未出卷')
  await expect(boardOnly.getByRole('button')).toHaveCount(0)

  await boardOnly.click()
  await expect(page.getByTestId('ticket-view')).toHaveCount(0)
})
