<template>
  <div class="page-container">
    <div class="list-header">
      <h3>合同列表</h3>
      <el-button type="primary" @click="$router.push('/contracts/upload')">
        <el-icon><Plus /></el-icon> 上传合同
      </el-button>
    </div>
    <el-divider />

    <!-- 搜索筛选栏 -->
    <el-card shadow="hover" class="search-card">
      <el-form :inline="true" :model="filters" class="search-form">
        <el-form-item label="合同名称">
          <el-input
            v-model="filters.keyword"
            placeholder="输入合同名称搜索"
            clearable
            @keyup.enter="handleSearch"
            @clear="handleSearch"
          />
        </el-form-item>
        <el-form-item label="合同类型">
          <el-select
            v-model="filters.contract_type"
            placeholder="全部类型"
            clearable
            style="width: 150px;"
            @change="handleSearch"
          >
            <el-option label="采购合同" value="采购合同" />
            <el-option label="销售合同" value="销售合同" />
            <el-option label="保密协议" value="保密协议" />
            <el-option label="服务外包合同" value="服务外包合同" />
            <el-option label="劳动合同" value="劳动合同" />
            <el-option label="其他合同" value="其他合同" />
          </el-select>
        </el-form-item>
        <el-form-item label="审核状态">
          <el-select
            v-model="filters.status"
            placeholder="全部状态"
            clearable
            style="width: 140px;"
            @change="handleSearch"
          >
            <el-option label="已上传" value="uploaded" />
            <el-option label="解析中" value="parsing" />
            <el-option label="审核中" value="auditing" />
            <el-option label="审核完成" value="completed" />
            <el-option label="待验收" value="reviewed" />
            <el-option label="已验收" value="approved" />
            <el-option label="审核失败" value="failed" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">
            <el-icon><Search /></el-icon> 搜索
          </el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 合同表格 -->
    <el-card shadow="hover" class="table-card">
      <el-table
        v-loading="loading"
        :data="contractList"
        stripe
        border
        style="width: 100%;"
      >
        <template #empty>
          <el-empty description="暂无合同数据" />
        </template>

        <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" @click="goToDetail(row.id)">{{ row.file_name }}</el-link>
          </template>
        </el-table-column>

        <el-table-column prop="contract_type" label="合同类型" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.contract_type" size="small">
              {{ typeLabel(row.contract_type) }}
            </el-tag>
            <span v-else style="color: #909399;">—</span>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="审核状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="created_at" label="上传时间" width="170">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="goToDetail(row.id)">
              查看
            </el-button>
            <el-button
              v-if="row.status === 'completed'"
              size="small"
              type="success"
              link
              @click="goToResult(row.id)"
            >
              审核结果
            </el-button>
            <el-popconfirm
              v-if="canDelete"
              title="确定要删除这份合同吗？"
              confirm-button-text="确认删除"
              cancel-button-text="取消"
              @confirm="handleDelete(row.id)"
            >
              <template #reference>
                <el-button size="small" type="danger" link>删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :page-sizes="[10, 20, 50]"
          :total="pagination.total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="fetchList"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import { getContractList, deleteContract } from '../api/contract.js'
import { formatTime } from '../utils/format.js'

const router = useRouter()

const role = localStorage.getItem('role') || ''
const canDelete = role === 'uploader' || role === 'admin'

const loading = ref(false)
const contractList = ref([])

const filters = reactive({
  keyword: '',
  contract_type: '',
  status: '',
})

const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0,
})

async function fetchList() {
  loading.value = true
  try {
    const res = await getContractList({
      page: pagination.page,
      page_size: pagination.page_size,
      keyword: filters.keyword || undefined,
      contract_type: filters.contract_type || undefined,
      status: filters.status || undefined,
    })
    contractList.value = res.data?.items || []
    pagination.total = res.data?.total || 0
    schedulePoll()
  } catch {
    console.warn('合同列表加载失败')
    ElMessage.error('加载合同列表失败，请检查网络连接')
  } finally {
    loading.value = false
  }
}

// ── 自动轮询：列表里有"审核中/解析中"的合同时，每 3 秒刷新一次，直到都完成 ──
let pollTimer = null
function schedulePoll() {
  const hasPending = contractList.value.some(c => c.status === 'auditing' || c.status === 'parsing')
  if (hasPending && !pollTimer) {
    pollTimer = setInterval(() => {
      fetchList()
    }, 3000)
  } else if (!hasPending && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function handleSearch() {
  pagination.page = 1
  fetchList()
}

// 切换每页条数时重置回第 1 页，避免旧页码在新 page_size 下越界返回空列表
function handleSizeChange() {
  pagination.page = 1
  fetchList()
}

function handleReset() {
  filters.keyword = ''
  filters.contract_type = ''
  filters.status = ''
  pagination.page = 1
  fetchList()
}

async function handleDelete(id) {
  try {
    await deleteContract(id)
    ElMessage.success('合同已删除')
    fetchList()
  } catch {
    // handled in request interceptor
  }
}

function goToDetail(id) {
  router.push(`/contracts/${id}`)
}

function goToResult(id) {
  router.push(`/audit/result/${id}`)
}

function typeLabel(type) {
  const map = {
    '采购合同': '采购合同',
    '销售合同': '销售合同',
    '保密协议': '保密协议',
    '服务外包合同': '服务外包合同',
    '劳动合同': '劳动合同',
    '其他合同': '其他合同',
    purchase: '采购合同', sales: '销售合同', nda: '保密协议',
    outsourcing: '服务外包合同', employment: '劳动合同', other: '其他合同',
  }
  return map[type] || type || '未分类'
}

function statusLabel(status) {
  const map = {
    uploaded: '已上传',
    parsing: '解析中',
    auditing: '审核中',
    completed: '审核完成',
    reviewed: '待验收',
    approved: '已验收',
    failed: '审核失败',
  }
  return map[status] || status
}

function statusTag(status) {
  const map = {
    uploaded: 'info',
    parsing: 'warning',
    auditing: 'warning',
    completed: 'success',
    reviewed: 'primary',
    approved: 'success',
    failed: 'danger',
  }
  return map[status] || 'info'
}


onMounted(() => {
  fetchList()
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
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

.search-card {
  margin-bottom: 16px;
}

.search-form {
  margin-bottom: 0;
}

.table-card {
  min-height: 400px;
}

.pagination-wrapper {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
