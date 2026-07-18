import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { submitFeedback, getFeedbackList } from '../api/contract.js'

/**
 * 反馈标注逻辑 — ContractDetail / AuditResultDetail 共用
 * @param {import('vue').ComputedRef<string|number>|import('vue').Ref<string|number>} contractId — 当前合同 ID
 */
export function useFeedback(contractId) {
  const feedbackRef = ref(null)

  function onFeedback(payload) {
    submitFeedback(payload).then(() => {
      ElMessage.success(`反馈已提交：${payload.action_type}`)
    }).catch((e) => {
      console.warn('反馈提交失败:', e)
      ElMessage.error('反馈提交失败，请重试')
    })
  }

  async function loadFeedback() {
    const id = contractId.value
    if (!id) return
    try {
      const res = await getFeedbackList(id)
      feedbackRef.value?.restore(res.data?.items || [])
    } catch {
      // 静默，加载失败不影响使用
    }
  }

  return { feedbackRef, onFeedback, loadFeedback }
}
