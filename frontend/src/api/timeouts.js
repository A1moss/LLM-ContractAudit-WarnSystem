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
