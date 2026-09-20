import test from 'node:test'
import assert from 'node:assert/strict'

import {
  CONTRACT_TYPES,
  TYPE_LABEL,
  UNCLASSIFIED_LABEL,
  typeLabel,
  isUnclassified,
} from './contractTypes.js'

/**
 * BUG-1 守卫：「无名合同」与「分类失败/待分类」不得混用。
 *
 * 背景：同一份合同重复上传时曾出现"一会儿建设工程合同、一会儿无名合同"。
 * 根因之一是后端分类失败时写入伪类型 `其他合同`（confidence=0），
 * 而前端把它归一显示为「无名合同」——失败被伪装成了一个正式法理类别。
 *
 * 本测试锁死三条不变式：
 *   1. 「无名合同」是正式类别，必须原样显示（分类成功的一种正常结果）；
 *   2. 「其他合同」/`other`/空值/未知值一律显示「待分类」，**不得**显示成「无名合同」；
 *   3. isUnclassified 以后端 classification_status 为准，缺失时按类型是否为空推导。
 */

test('无名合同是正式类别，原样显示', () => {
  assert.ok(CONTRACT_TYPES.includes('无名合同'))
  assert.equal(typeLabel('无名合同'), '无名合同')
  assert.equal(TYPE_LABEL['无名合同'], '无名合同')
})

test('分类失败态一律显示「待分类」，绝不显示成「无名合同」', () => {
  for (const legacy of ['其他合同', 'other', 'unclassified', '未分类', '待分类']) {
    assert.equal(typeLabel(legacy), UNCLASSIFIED_LABEL, `${legacy} 应显示待分类`)
    assert.notEqual(typeLabel(legacy), '无名合同')
  }
})

test('空值/未知类型显示「待分类」', () => {
  assert.equal(typeLabel(null), UNCLASSIFIED_LABEL)
  assert.equal(typeLabel(undefined), UNCLASSIFIED_LABEL)
  assert.equal(typeLabel(''), UNCLASSIFIED_LABEL)
})

test('isUnclassified：后端状态优先，缺失时按类型推导', () => {
  // 显式失败
  assert.equal(isUnclassified({ classification_status: 'failed', contract_type: null }), true)
  // 显式成功 + 模型判定无名合同 ⇒ 不是"未分类"
  assert.equal(isUnclassified({ classification_status: 'success', contract_type: '无名合同' }), false)
  // 手工指定
  assert.equal(isUnclassified({ classification_status: 'manual', contract_type: '建设工程合同' }), false)
  // 历史数据（无状态字段）
  assert.equal(isUnclassified({ contract_type: '无名合同' }), false)
  assert.equal(isUnclassified({ contract_type: null }), true)
  assert.equal(isUnclassified({}), true)
  assert.equal(isUnclassified(null), true)
})
