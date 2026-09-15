<template>
  <div class="page-container">
    <!-- 页头 -->
    <div class="a24-page-header">
      <div>
        <div class="crumb">首页 / <b>用户管理</b></div>
        <div class="title-row">
          <span class="icon"><el-icon><UserFilled /></el-icon></span>
          <h3>用户管理</h3>
        </div>
        <div class="desc">查看系统用户并分配角色（仅管理员可见、可操作）</div>
      </div>
      <div class="actions">
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 非管理员提示（仅 UI 兜底；真正的权限边界在后端 require_role("admin")） -->
    <el-alert
      v-if="!isAdmin"
      title="仅管理员可访问用户管理"
      description="你的账号不是管理员。即使手工调用接口，后端也会返回 403。"
      type="error"
      show-icon
      :closable="false"
      class="section-card"
    />

    <el-card shadow="hover" class="section-card">
      <div class="a24-card-head">
        <span class="t">用户列表 <small>共 {{ users.length }} 个账号</small></span>
      </div>

      <el-table v-loading="loading" :data="users" stripe border>
        <template #empty><el-empty description="暂无用户" /></template>
        <el-table-column prop="username" label="用户名" min-width="150">
          <template #default="{ row }">
            <span>{{ row.username }}</span>
            <el-tag v-if="isMe(row)" size="small" type="info" effect="plain" style="margin-left:6px">我</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
        <el-table-column label="当前角色" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="ROLE_TAG_TYPES[row.role] || 'info'" effect="light">{{ ROLE_LABELS[row.role] || row.role }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="修改角色" width="260" align="center">
          <template #default="{ row }">
            <template v-if="isMe(row)">
              <span class="self-hint">不能修改自己的角色</span>
            </template>
            <template v-else>
              <el-select v-model="row.draftRole" size="small" style="width:120px">
                <el-option v-for="o in ROLE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
              <el-button
                size="small"
                type="primary"
                style="margin-left:6px"
                :disabled="row.draftRole === row.role"
                :loading="savingId === row.id"
                @click="save(row)"
              >保存</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <div class="tip">
        说明：系统必须至少保留一名管理员，且管理员不能修改自己的角色（避免把自己锁在系统外）；
        把他人提升为管理员是允许的，可存在多个管理员。
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UserFilled } from '@element-plus/icons-vue'
import { getUsers, updateUserRole } from '../api/user.js'

const ROLE_LABELS = { uploader: '上传者', reviewer: '审核人', approver: '验收人', admin: '管理员' }
const ROLE_TAG_TYPES = { uploader: 'primary', reviewer: 'warning', approver: 'success', admin: 'danger' }
const ROLE_OPTIONS = [
  { value: 'uploader', label: '上传者' },
  { value: 'reviewer', label: '审核人' },
  { value: 'approver', label: '验收人' },
  { value: 'admin', label: '管理员' },
]

const users = ref([])
const loading = ref(false)
const savingId = ref(null)

// 仅用于 UI 显隐/禁用；localStorage 可被篡改，因此后端 require_role("admin") 才是权限边界
const myUsername = ref(localStorage.getItem('username') || '')
const isAdmin = computed(() => (localStorage.getItem('role') || '') === 'admin')

function isMe(row) {
  return !!myUsername.value && row.username === myUsername.value
}

async function load() {
  loading.value = true
  try {
    const res = await getUsers()
    users.value = (res.data?.items || []).map(u => ({ ...u, draftRole: u.role }))
  } catch {
    users.value = []   // 403/网络错误已由 request 拦截器提示
  } finally {
    loading.value = false
  }
}

async function save(row) {
  savingId.value = row.id
  try {
    const res = await updateUserRole(row.id, row.draftRole)
    const updated = res.data || {}
    row.role = updated.role || row.draftRole
    row.draftRole = row.role
    ElMessage.success(`已将「${row.username}」设为${ROLE_LABELS[row.role] || row.role}`)
    // 若（理论上）改到的是自己，同步本地角色——正常情况下后端会拒绝自我降权
    if (isMe(row)) {
      localStorage.setItem('role', row.role)
    }
  } catch {
    row.draftRole = row.role   // 失败回滚下拉选择
  } finally {
    savingId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.page-container {
  max-width: 1200px;
  padding: 24px;
  margin: 0 auto;
}

.section-card {
  margin-bottom: 18px;
}

.self-hint {
  font-size: 12.5px;
  color: #9AA4B8;
}

.tip {
  margin-top: 12px;
  font-size: 12.5px;
  color: #9AA4B8;
  line-height: 1.7;
}
</style>
