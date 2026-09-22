// 给「UI 改版」拍证据：两个栈各截几张（宽屏），我自己先看一眼再交给用户。
// 放在 scratch/（gitignore）里，不进 e2e 套件，免得改了测试计数。
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const OUT = 'F:/workspace/bagu-trainer/scratch/shots'
mkdirSync(OUT, { recursive: true })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function shoot(page, name) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true })
  console.log('shot', name)
}

async function run(base, tag, { stage, submit }) {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  await page.goto(base, { waitUntil: 'networkidle' })
  await page.waitForTimeout(600)
  await shoot(page, `${tag}-1-board`)

  // 已经入过学就不点（后端可能还留着上一次的状态）。
  const enrollBtn = page.getByRole('button', { name: /入学/ })
  if ((await enrollBtn.count()) > 0) {
    await enrollBtn.click()
    await page.waitForTimeout(800)
  }
  try {
    await page.getByTestId(stage).click()
  } catch (e) {
    console.log(`${tag}: 点不到 ${stage} —`, e.message.split('\n')[0])
    await shoot(page, `${tag}-x-nostage`)
    await browser.close()
    return
  }
  await page.waitForTimeout(900)
  await shoot(page, `${tag}-2-ticket`)
  // 题面下半部分（改动面/判据/提示）单独拍一张：长题面的可读性主要看这里
  await page.locator('#ticket-grader').scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(300)
  await shoot(page, `${tag}-2b-ticket-lower`)

  if (submit) {
    try {
      if ((await page.getByRole('button', { name: '开始本阶段' }).count()) > 0) {
        await page.getByRole('button', { name: '开始本阶段' }).click()
        await page.waitForTimeout(700)
      }
      await page.getByRole('button', { name: '拉取本地工作区' }).click()
      await page.waitForTimeout(1500)
      await page.getByRole('button', { name: '查看 diff' }).click()
      await page.waitForTimeout(900)
      await shoot(page, `${tag}-3-workspace`)
      await page.getByRole('button', { name: /提交（跑阅卷桩）/ }).click()
      // 桩是瞬时的；真阅卷要等，给它更长时间
      await page.waitForTimeout(tag === 'demo' ? 2000 : 150_000)
      await shoot(page, `${tag}-4-graded`)
      const hint = page.locator('.hint summary').first()
      if ((await hint.count()) > 0) {
        await hint.click()
        await page.waitForTimeout(400)
        await shoot(page, `${tag}-5-hint-open`)
      }
    } catch (e) {
      console.log(`${tag}: flow stopped —`, e.message.split('\n')[0])
      await shoot(page, `${tag}-x-stuck`)
    }
  }
  await browser.close()
}

await run(process.env.DEMO_URL ?? 'http://127.0.0.1:5173/', 'demo', {
  stage: 'stage-stage-00-onboarding',
  submit: true,
})
if (process.env.REAL_URL) {
  await run(process.env.REAL_URL, 'real', {
    stage: 'stage-stage-01-product-query-index',
    submit: process.env.REAL_SUBMIT === '1',
  })
}
await sleep(200)
