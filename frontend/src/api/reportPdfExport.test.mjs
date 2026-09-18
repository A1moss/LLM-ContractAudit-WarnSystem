import test from 'node:test'
import assert from 'node:assert/strict'

/**
 * 审核报告 PDF 导出（P0-1）前端契约测试。
 *
 * 只测**纯逻辑**（不依赖 Vue / 浏览器 / 真实网络）：
 *  - 下载文件名解析（RFC 5987 中文文件名 + ASCII 回退名）；
 *  - blob 响应下后端错误详情的读取（responseType:'blob' 时 data 也是 Blob，
 *    直接读 detail 会拿到 undefined，用户就只看到无信息量的兜底文案）。
 */

// ── 复刻 utils/request.js 拦截器里对 Blob 文件名解析的那段逻辑 ──
// （Node 环境没有 window/document，无法 import 真实模块；这里验证**判定规则本身**，
//   真实模块与之逐行一致，见 utils/request.js 的 saveBlob / 响应拦截器。）
function parseDisposition(disp) {
  const out = {}
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disp)
  if (star?.[1]) {
    try {
      out.filename = decodeURIComponent(star[1])
    } catch {
      /* 非法编码 → 走 ASCII 回退 */
    }
  }
  if (!out.filename) {
    const plain = /filename="?([^";]+)"?/i.exec(disp)
    if (plain?.[1]) out.filename = plain[1]
  }
  return out
}

test('P0-1: 中文文件名走 RFC 5987，能被正确解码为 合同名_审核报告.pdf', () => {
  const name = '服务外包合同_审核报告.pdf'
  const disp = `attachment; filename="contract-1-audit-report.pdf"; filename*=UTF-8''${encodeURIComponent(name)}`
  assert.equal(parseDisposition(disp).filename, name)
})

test('P0-1: 无 filename* 时回退到 ASCII filename，不会得到 undefined', () => {
  const disp = 'attachment; filename="contract-7-audit-report.pdf"'
  assert.equal(parseDisposition(disp).filename, 'contract-7-audit-report.pdf')
})

test('P0-1: filename* 编码非法时回退 ASCII，不抛异常', () => {
  const disp = 'attachment; filename="fallback.pdf"; filename*=UTF-8\'\'%E4%B8%'
  const parsed = parseDisposition(disp)
  assert.ok(parsed.filename)
  assert.ok(parsed.filename.includes('fallback') || parsed.filename.length > 0)
})

test('P0-1: 文件名含 URL 敏感字符时编码/解码后逐字一致', () => {
  const name = 'a b&c#d+合同_审核报告.pdf'
  const disp = `attachment; filename="x.pdf"; filename*=UTF-8''${encodeURIComponent(name)}`
  assert.equal(parseDisposition(disp).filename, name)
})

// ── blob 错误体读取逻辑（与 utils/request.js readApiError 同规则） ──
async function readApiErrorLike(error, fallback = '请求失败') {
  const data = error?.response?.data
  if (!data) return fallback
  if (typeof data === 'string') return data || fallback
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    try {
      const text = await data.text()
      if (!text) return fallback
      try {
        const parsed = JSON.parse(text)
        return parsed?.detail || parsed?.message || fallback
      } catch {
        return text.slice(0, 200) || fallback
      }
    } catch {
      return fallback
    }
  }
  return data.detail || data.message || fallback
}

test('P0-1: blob 响应体里的后端 detail 能被读出（而不是展示兜底文案）', async () => {
  const blob = new Blob([JSON.stringify({ detail: 'no audit report found' })], {
    type: 'application/json',
  })
  const msg = await readApiErrorLike({ response: { status: 404, data: blob } }, '审核报告导出失败')
  assert.equal(msg, 'no audit report found')
})

test('P0-1: 普通 JSON 错误体照常读出 detail', async () => {
  const msg = await readApiErrorLike(
    { response: { status: 500, data: { detail: '审核报告 PDF 生成失败：xx' } } },
    '审核报告导出失败',
  )
  assert.equal(msg, '审核报告 PDF 生成失败：xx')
})

test('P0-1: 空 Blob / 无 response 时给出兜底文案，绝不显示成功', async () => {
  const empty = new Blob([], { type: 'application/json' })
  assert.equal(
    await readApiErrorLike({ response: { status: 500, data: empty } }, '审核报告导出失败'),
    '审核报告导出失败',
  )
  assert.equal(await readApiErrorLike(new Error('Network Error'), '审核报告导出失败'), '审核报告导出失败')
})

test('P0-1: 非 JSON 的 blob 错误体降级为文本片段，不吞掉信息', async () => {
  const blob = new Blob(['Internal Server Error'], { type: 'text/plain' })
  const msg = await readApiErrorLike({ response: { status: 500, data: blob } }, '兜底')
  assert.equal(msg, 'Internal Server Error')
})

// ── 超时配置 ──
test('P0-1: REPORT_PDF_TIMEOUT 为正数且不小于默认 60s 基线', async () => {
  const { REPORT_PDF_TIMEOUT } = await import('./timeouts.js')
  assert.ok(Number.isFinite(REPORT_PDF_TIMEOUT) && REPORT_PDF_TIMEOUT > 0)
  assert.ok(REPORT_PDF_TIMEOUT >= 60_000, `REPORT_PDF_TIMEOUT=${REPORT_PDF_TIMEOUT}`)
})
