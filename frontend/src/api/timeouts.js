/**
 * 长耗时接口的前端超时（毫秒），按后端实际最坏耗时单独配置（BUG-037）。
 *
 * 默认全局超时 60s 见 ../utils/request.js；以下接口后端是同步长耗时操作，
 * 若沿用 60s，前端会先报超时而后端仍在跑（结果丢失 / 重复消耗），故逐个放开。
 */

// POST /contracts/{id}/revise：Leader → Follower → Self-QA 三次串行 LLM，
// 每次 30s 上限，最坏约 90s；留 30s 余量。
export const REVISE_TIMEOUT = 120_000

// GET /contracts/{id}/clause-comparison（后端无缓存时当场生成；含 POST /compare、
// POST /clause-comparison）：按正文 N 块（split_chunks(6000)）+ 6 并发，耗时随 N 增长，
// 不假定固定 90s。300s 是当前按实际并发模型设置的合理上限，超过后仍可能超时。
export const COMPARE_TIMEOUT = 300_000

// GET /contracts/{id}/file：.docx → PDF（LibreOffice 自身 timeout=60s）。60s + 60s 余量。
export const FILE_TIMEOUT = 120_000

// POST /contracts/{id}/locate-clause：纯文本匹配（无 LLM、无写库），
// 后端只做几次子串查找，正常耗时 < 1s；给 20s 足以覆盖超长合同。
export const LOCATE_TIMEOUT = 20_000

// POST /contracts/{id}/overview/plan：单次 LLM，但 prompt 含整份合同正文（最长约 24000 字）
// + 全部专项会话结果，输入远大于 /revise；给 180s 覆盖长合同与限流重试。
export const OVERVIEW_PLAN_TIMEOUT = 180_000

// GET /contracts/{id}/audit-report/pdf：后端纯排版（不调 LLM / 不查 RAG / 不重算指标），
// 长合同（十页以上）实测约 1–3s；给 60s 覆盖冷启动与超大报告。
export const REPORT_PDF_TIMEOUT = 60_000
