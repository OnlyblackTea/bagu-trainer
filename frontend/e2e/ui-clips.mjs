// 1:1 局部截图：整页缩略图看不清字，改版质量要按区域看。
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const OUT = 'F:/workspace/bagu-trainer/scratch/shots'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
await page.goto(process.env.URL ?? 'http://127.0.0.1:5174/', { waitUntil: 'networkidle' })
await page.waitForTimeout(700)

const enroll = page.getByRole('button', { name: /入学/ })
if ((await enroll.count()) > 0) {
  await enroll.click()
  await page.waitForTimeout(700)
}
await page.getByTestId('stage-stage-01-product-query-index').click()
await page.waitForTimeout(900)

async function clip(selector, name, pad = 8) {
  const el = page.locator(selector).first()
  if ((await el.count()) === 0) {
    console.log('missing', selector)
    return
  }
  await el.scrollIntoViewIfNeeded()
  await page.waitForTimeout(250)
  const box = await el.boundingBox()
  if (!box) {
    console.log('no box', selector)
    return
  }
  await page.screenshot({
    path: `${OUT}/clip-${name}.png`,
    clip: {
      x: Math.max(0, box.x - pad),
      y: Math.max(0, box.y - pad),
      width: Math.min(1440 - box.x, box.width + pad * 2),
      height: Math.min(1200, box.height + pad * 2),
    },
  })
  console.log('clip', name, Math.round(box.width), 'x', Math.round(box.height))
}

await clip('.rail--nav', 'nav')
await clip('.ticket__head', 'ticket-head')
await clip('#ticket-problem', 'ticket-problem')
await clip('#ticket-accept', 'ticket-accept')
await clip('#ticket-hints', 'ticket-hints')

// 历史里那次尝试的报告（展开）
const summary = page.locator('.attempt details summary').first()
if ((await summary.count()) > 0) {
  await summary.click()
  await page.waitForTimeout(400)
  await clip('.attempt', 'history-attempt', 6)
} else {
  console.log('no history attempt to open')
}

await browser.close()
