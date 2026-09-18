<template>
  <div class="page-container">
    <!-- 页头 -->
    <div class="a24-page-header">
      <div>
        <div class="crumb">首页 / <b>模板管理</b></div>
        <div class="title-row">
          <span class="icon"><el-icon><Files /></el-icon></span>
          <h3>标准条款模板管理</h3>
        </div>
        <div class="desc">维护各类合同的标准条款范本，用于 RAG 检索与条款比对</div>
      </div>
      <div class="actions">
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon> 新建模板
        </el-button>
      </div>
    </div>

    <el-card shadow="hover">
      <el-table v-loading="loading" :data="templates" stripe border>
        <template #empty><el-empty description="暂无模板" /></template>
        <el-table-column prop="name" label="模板名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="contract_type" label="合同类型" width="130" />
        <el-table-column label="条款数" width="100">
          <template #default="{ row }">
            {{ clauseCount(row.clauses) }}
          </template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="info" link @click="openHistory(row)">历史</el-button>
            <el-popconfirm title="确定删除该模板？" @confirm="handleDelete(row.id)">
              <template #reference>
                <el-button size="small" type="danger" link>删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建/编辑对话框 -->
    <el-dialog v-model="dialog.visible" :title="dialog.isEdit ? '编辑模板' : '新建模板'" width="640px">
      <el-form label-position="top">
        <el-form-item label="模板名称">
          <el-input v-model="dialog.name" placeholder="如：买卖合同标准条款" />
        </el-form-item>
        <el-form-item label="合同类型">
          <el-select v-model="dialog.contract_type" style="width: 100%">
            <el-option v-for="t in CONTRACT_TYPES" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="条款内容（JSON）">
          <el-input
            v-model="dialog.clausesText"
            type="textarea"
            :rows="10"
            placeholder='如：{"验收标准": "合同应约定明确的验收标准与验收流程", "付款条件": "..."}'
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 历史版本对话框 -->
    <el-dialog v-model="history.visible" :title="`历史版本 - ${history.name}`" width="720px">
      <el-alert
        v-if="history.error" type="error" show-icon :closable="false"
        :title="history.error" class="hist-alert"
      >
        <el-button size="small" @click="reloadHistory">重试</el-button>
      </el-alert>
      <el-table v-loading="history.loading" :data="history.items" stripe border>
        <template #empty>
          <el-empty :description="history.error ? '历史版本加载失败' : '暂无历史版本'" />
        </template>
        <el-table-column prop="version" label="版本" width="90">
          <template #default="{ row }">
            v{{ row.version }}
            <el-tag v-if="row.id === history.latestId" size="small" type="success" style="margin-left:6px">当前</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="条款数" width="100">
          <template #default="{ row }">{{ clauseCount(row.clauses) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="上一版本 ID" width="120">
          <template #default="{ row }">{{ row.previous_version_id ?? '—' }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="history.visible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Files } from '@element-plus/icons-vue'
import { getTemplates, getTemplateHistory, createTemplate, updateTemplate, deleteTemplate } from '../api/template.js'
import { CONTRACT_TYPES } from '../constants/contractTypes.js'
import { formatTime } from '../utils/format.js'

const loading = ref(false)
const saving = ref(false)
const templates = ref([])

const dialog = reactive({
  visible: false,
  isEdit: false,
  id: null,
  name: '',
  contract_type: '买卖合同',
  clausesText: '',
})

const history = reactive({
  visible: false,
  loading: false,
  error: '',
  name: '',
  templateId: null,
  latestId: null,
  items: [],
})

function clauseCount(clauses) {
  if (!clauses) return 0
  if (Array.isArray(clauses)) return clauses.length
  if (typeof clauses === 'object') return Object.keys(clauses).length
  return 0
}

async function fetchList() {
  loading.value = true
  try {
    const res = await getTemplates()
    templates.value = res.data?.items || []
  } catch {
    ElMessage.error('加载模板失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  dialog.visible = true
  dialog.isEdit = false
  dialog.id = null
  dialog.name = ''
  dialog.contract_type = '买卖合同'
  dialog.clausesText = ''
}

function openEdit(row) {
  dialog.visible = true
  dialog.isEdit = true
  dialog.id = row.id
  dialog.name = row.name
  dialog.contract_type = row.contract_type
  dialog.clausesText = JSON.stringify(row.clauses, null, 2)
}

/**
 * 打开「历史」：查询该模板的完整版本链（后端 GET /templates/{id}/history）。
 *
 * 展示口径（与后端返回结构严格一致，不新增第二套字段）：
 *   - 后端返回 items 按**从最早到最新**排列（`templates.template_history` 先沿
 *     previous_version_id 向前追溯再 reverse），因此被点击的这一行就是**当前最新版**；
 *   - 列表里的「当前」标记 = 被点击的模板 id（旧实现取 items 的最后一个，等于把
 *     **最早**那版标成了「当前」，与真实语义相反，这里一并修正）；
 *   - 无历史版本时显示空状态；请求失败时显示错误 + 重试，而不是让用户误以为「暂无历史版本」。
 */
async function openHistory(row) {
  history.visible = true
  history.templateId = row.id
  history.name = row.name
  history.latestId = row.id
  await fetchHistory(row.id)
}

/** 重新拉取当前模板的历史版本（错误态「重试」用） */
async function reloadHistory() {
  if (history.templateId == null) return
  await fetchHistory(history.templateId)
}

async function fetchHistory(templateId) {
  history.loading = true
  history.error = ''
  history.items = []
  try {
    const res = await getTemplateHistory(templateId)
    const items = res?.data?.items
    history.items = Array.isArray(items) ? items : []
    // 兜底：后端若按其它顺序返回，以链尾（最新）为准；拿不到就保持被点击的模板 id
    const last = history.items[history.items.length - 1]
    if (last?.id != null) history.latestId = last.id
  } catch (e) {
    // 拦截器已弹过提示，这里保留行内错误态，避免"失败"被显示成"暂无历史版本"
    history.error = e?.response?.data?.detail || '历史版本加载失败'
  } finally {
    history.loading = false
  }
}

async function handleSave() {
  if (!dialog.name.trim()) {
    ElMessage.warning('请填写模板名称')
    return
  }
  let clauses
  try {
    clauses = dialog.clausesText.trim() ? JSON.parse(dialog.clausesText) : {}
  } catch {
    ElMessage.error('条款 JSON 格式不正确')
    return
  }

  saving.value = true
  try {
    if (dialog.isEdit) {
      const res = await updateTemplate(dialog.id, { name: dialog.name, clauses })
      ElMessage.success(`模板已更新（新版本 v${res.data?.version ?? ''}）`)
    } else {
      await createTemplate({ name: dialog.name, contract_type: dialog.contract_type, clauses })
      ElMessage.success('模板已创建')
    }
    dialog.visible = false
    fetchList()
  } catch {
    // 错误已在拦截器处理
  } finally {
    saving.value = false
  }
}

async function handleDelete(id) {
  try {
    await deleteTemplate(id)
    ElMessage.success('模板已删除')
    fetchList()
  } catch {
    // 错误已在拦截器处理
  }
}

onMounted(() => fetchList())
</script>

<style scoped>
.page-container {
  max-width: 1200px;
  padding: 24px;
  margin: 0 auto;
}
.hist-alert { margin-bottom: 10px; }
</style>
