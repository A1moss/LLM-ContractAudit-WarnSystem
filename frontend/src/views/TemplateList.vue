<template>
  <div class="page-container">
    <div class="list-header">
      <h3>标准条款模板管理</h3>
      <el-button type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon> 新建模板
      </el-button>
    </div>
    <el-divider />

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
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openEdit(row)">编辑</el-button>
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
          <el-input v-model="dialog.name" placeholder="如：采购合同标准条款" />
        </el-form-item>
        <el-form-item label="合同类型">
          <el-select v-model="dialog.contract_type" style="width: 100%">
            <el-option label="采购合同" value="采购合同" />
            <el-option label="销售合同" value="销售合同" />
            <el-option label="保密协议" value="保密协议" />
            <el-option label="服务外包合同" value="服务外包合同" />
            <el-option label="劳动合同" value="劳动合同" />
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { getTemplates, createTemplate, updateTemplate, deleteTemplate } from '../api/template.js'

const loading = ref(false)
const saving = ref(false)
const templates = ref([])

const dialog = reactive({
  visible: false,
  isEdit: false,
  id: null,
  name: '',
  contract_type: '采购合同',
  clausesText: '',
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
  dialog.contract_type = '采购合同'
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
      await updateTemplate(dialog.id, { name: dialog.name, clauses })
      ElMessage.success('模板已更新（版本 +1）')
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
.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
