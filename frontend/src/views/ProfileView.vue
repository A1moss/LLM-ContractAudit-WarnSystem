<template>
  <div class="page-container">
    <!-- 页头 -->
    <div class="a24-page-header">
      <div>
        <div class="crumb">首页 / <b>个人信息</b></div>
        <div class="title-row">
          <span class="icon"><el-icon><User /></el-icon></span>
          <h3>个人信息</h3>
        </div>
        <div class="desc">管理你的账户信息与 DeepSeek API 配置</div>
      </div>
    </div>

    <!-- 账户信息 -->
    <el-card shadow="hover" class="section-card">
      <div class="a24-card-head">
        <span class="t">账户信息</span>
      </div>
      <div class="info-grid">
        <div class="info-item">
          <span class="info-ic" style="background:#E8EBF9;color:#1935C3"><el-icon><User /></el-icon></span>
          <div class="info-body">
            <div class="k">用户名</div>
            <div class="v">{{ username }}</div>
          </div>
        </div>
        <div class="info-item">
          <span class="info-ic" style="background:#E6F4FF;color:#1B70F0"><el-icon><Message /></el-icon></span>
          <div class="info-body">
            <div class="k">注册邮箱</div>
            <div class="v">{{ email || '未记录' }}</div>
          </div>
        </div>
        <div class="info-item">
          <span class="info-ic" style="background:#FFF1F0;color:#E60012"><el-icon><Star /></el-icon></span>
          <div class="info-body">
            <div class="k">角色</div>
            <div class="v"><el-tag :type="roleTagType" effect="light">{{ roleLabel }}</el-tag></div>
          </div>
        </div>
      </div>
      <el-alert
        v-if="!email"
        type="info"
        :closable="false"
        show-icon
        title="未检测到注册邮箱"
        description="旧登录会话未保存邮箱信息，请退出后重新登录一次即可自动同步。"
        class="email-alert"
      />
    </el-card>

    <!-- DeepSeek API 配置 -->
    <el-card shadow="hover" class="section-card">
      <div class="a24-card-head">
        <span class="t">DeepSeek API 配置 <small>AI 审核引擎调用大模型所需</small></span>
      </div>

      <!-- 已配置状态（后端只回状态，不回 Key 本身，因此这里不展示任何 Key 片段） -->
      <div v-if="keyConfigured && !editing" class="key-saved">
        <div class="key-status">
          <span class="status-dot"></span>
          <span>已配置</span>
        </div>
        <div class="key-actions">
          <el-button type="primary" plain @click="startEdit">
            <el-icon><EditPen /></el-icon> 修改
          </el-button>
          <el-button type="danger" plain :loading="saving" @click="clearKey">
            <el-icon><Delete /></el-icon> 删除
          </el-button>
        </div>
      </div>

      <!-- 输入状态 -->
      <div v-else class="key-input">
        <el-input
          v-model="apiKeyDraft"
          type="password"
          show-password
          placeholder="请输入你自己的 DeepSeek API Key（以 sk- 开头）"
          clearable
          @keyup.enter="saveKey"
        >
          <template #prefix><el-icon><Key /></el-icon></template>
        </el-input>
        <el-button type="primary" :loading="saving" @click="saveKey">
          <el-icon><CircleCheck /></el-icon> 保存
        </el-button>
        <el-button v-if="keyConfigured" @click="cancelEdit">取消</el-button>
      </div>

      <div class="key-hint">
        <el-icon><InfoFilled /></el-icon>
        <span>
          保存后，该 Key 将用于<strong>当前账号</strong>调用 DeepSeek，<strong>不会展示给其他用户</strong>（管理员也只看得到「已配置」）。
          服务器加密存储，接口不回显明文。<br />
          优先级：<strong>个人 Key &gt; 系统默认 Key</strong>（<code>.env</code> 的 <code>DEEPSEEK_API_KEY</code>）。
          删除个人 Key 后自动回退系统默认 Key。
        </span>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { User, Message, Star, Key, Delete, EditPen, CircleCheck, InfoFilled } from '@element-plus/icons-vue'
import { getProfile, saveMyDeepseekKey, deleteMyDeepseekKey } from '../api/profile.js'

const username = ref(localStorage.getItem('username') || '—')
const email = ref(localStorage.getItem('email') || '')
const role = ref(localStorage.getItem('role') || '')

const ROLE_LABELS = { uploader: '上传者', reviewer: '审核人', approver: '验收人', admin: '管理员' }
const ROLE_TAG_TYPES = { uploader: 'primary', reviewer: 'warning', approver: 'success', admin: 'danger' }

const roleLabel = computed(() => ROLE_LABELS[role.value] || role.value || '—')
const roleTagType = computed(() => ROLE_TAG_TYPES[role.value] || 'info')

// 个人 Key：只从后端拿「是否已配置」的布尔状态；**不在前端保存真实 Key**
const keyConfigured = ref(false)
const apiKeyDraft = ref('')
const editing = ref(false)
const saving = ref(false)

// 兼容旧版本地缓存：把历史遗留的明文 Key 从 localStorage 清掉（安全清理）
function purgeLegacyLocalKey() {
  if (localStorage.getItem('deepseek_api_key')) {
    localStorage.removeItem('deepseek_api_key')
  }
}

async function loadProfile() {
  purgeLegacyLocalKey()
  try {
    const res = await getProfile()
    const d = res.data || {}
    keyConfigured.value = !!d.deepseek_key_configured
    if (d.username) username.value = d.username
    if (d.email) email.value = d.email
    if (d.role) role.value = d.role
  } catch {
    // 未登录/网络异常：保持默认展示，由 request 拦截器提示
  }
}

function startEdit() {
  apiKeyDraft.value = ''   // 绝不回填历史 Key（后端也不返回）
  editing.value = true
}

function cancelEdit() {
  apiKeyDraft.value = ''
  editing.value = false
}

async function saveKey() {
  const v = apiKeyDraft.value.trim()
  if (!v) {
    ElMessage.warning('请输入 API Key')
    return
  }
  if (v.length < 8) {
    ElMessage.warning('API Key 长度不足，请检查是否完整')
    return
  }
  saving.value = true
  try {
    const res = await saveMyDeepseekKey(v)
    keyConfigured.value = !!(res.data || {}).deepseek_key_configured
    editing.value = false
    apiKeyDraft.value = ''
    ElMessage.success('已保存，将用于当前账号调用 DeepSeek')
  } catch {
    // 错误已由 request 拦截器提示
  } finally {
    saving.value = false
  }
}

async function clearKey() {
  saving.value = true
  try {
    await deleteMyDeepseekKey()
    keyConfigured.value = false
    apiKeyDraft.value = ''
    editing.value = false
    ElMessage.success('已删除个人 Key，后续将使用系统默认 Key')
  } catch {
    // 错误已由 request 拦截器提示
  } finally {
    saving.value = false
  }
}

onMounted(loadProfile)
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

/* —— 账户信息 —— */
.info-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  padding: 20px 24px;
}
.info-item {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--a24-border);
  border-radius: 10px;
  padding: 16px;
}
.info-ic {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.info-body .k { font-size: 12px; color: var(--a24-muted); }
.info-body .v { font-size: 15px; font-weight: 600; color: var(--a24-heading); margin-top: 2px; }
.email-alert { margin: 0 24px 20px; }

/* —— DeepSeek API —— */
.key-saved {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px;
}
.key-status {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  color: var(--a24-text);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #67C23A;
  box-shadow: 0 0 0 3px rgba(103, 194, 58, 0.15);
}
.masked-key {
  background: #F5F8FC;
  border: 1px solid var(--a24-border);
  padding: 2px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--a24-primary);
  font-family: monospace;
}
.key-actions { display: flex; gap: 10px; }

.key-input {
  display: flex;
  gap: 10px;
  padding: 20px 24px;
}
.key-input .el-input { flex: 1; max-width: 480px; }

.key-hint {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 0 24px 20px;
  font-size: 12.5px;
  color: var(--a24-muted);
  line-height: 1.6;
}
.key-hint .el-icon { color: #E6A23C; margin-top: 2px; flex-shrink: 0; }
.key-hint code {
  background: #F0F2F7;
  padding: 0 4px;
  border-radius: 4px;
  font-family: monospace;
}

@media (max-width: 900px) {
  .info-grid { grid-template-columns: 1fr; }
  .key-saved { flex-direction: column; align-items: flex-start; }
  .key-input { flex-direction: column; }
  .key-input .el-input { max-width: none; }
}
</style>
