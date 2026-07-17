<template>
  <div class="login-wrapper">
    <el-card class="login-card" shadow="always">
      <h2 style="text-align: center; margin-bottom: 24px;">A24 合同智能审核系统</h2>

      <el-tabs v-model="activeTab" class="login-tabs">
        <!-- ── 登录 Tab ── -->
        <el-tab-pane label="登录" name="login">
          <el-form ref="loginFormRef" :model="loginForm" :rules="loginRules" label-position="top" @keyup.enter="handleLogin">
            <el-form-item label="用户名" prop="username">
              <el-input v-model="loginForm.username" placeholder="请输入用户名" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="loginForm.password" type="password" show-password placeholder="请输入密码" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" style="width: 100%;" :loading="loading" @click="handleLogin">
                登 录
              </el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- ── 注册 Tab ── -->
        <el-tab-pane label="注册" name="register">
          <el-form ref="regFormRef" :model="regForm" :rules="regRules" label-position="top">
            <el-form-item label="用户名" prop="username">
              <el-input v-model="regForm.username" placeholder="2-50 个字符" />
            </el-form-item>
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="regForm.email" placeholder="请输入邮箱" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input v-model="regForm.password" type="password" show-password placeholder="至少 6 位" />
            </el-form-item>
            <el-form-item label="确认密码" prop="confirmPassword">
              <el-input v-model="regForm.confirmPassword" type="password" show-password placeholder="再次输入密码" />
            </el-form-item>
            <el-form-item>
              <el-button type="success" style="width: 100%;" :loading="loading" @click="handleRegister">
                注 册
              </el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../utils/request.js'

const router = useRouter()
const activeTab = ref('login')
const loading = ref(false)

// ── 已有 token 直接跳首页 ──
onMounted(() => {
  if (localStorage.getItem('token')) {
    router.replace('/')
  }
})

// ── 登录 ──
const loginFormRef = ref(null)
const loginForm = ref({ username: '', password: '' })
const loginRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await loginFormRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const res = await request.post('/auth/login', {
      username: loginForm.value.username,
      password: loginForm.value.password,
    })
    localStorage.setItem('token', res.data.token)
    localStorage.setItem('username', res.data.user.username)
    ElMessage.success('登录成功')
    router.replace('/')
  } catch {
    // 错误已在 request 拦截器中处理
  } finally {
    loading.value = false
  }
}

// ── 注册 ──
const regFormRef = ref(null)
const regForm = ref({ username: '', email: '', password: '', confirmPassword: '' })

const validateConfirmPassword = (_rule, value, callback) => {
  if (value !== regForm.value.password) {
    callback(new Error('两次密码输入不一致'))
  } else {
    callback()
  }
}

const regRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 50, message: '用户名 2-50 个字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirmPassword, trigger: 'blur' },
  ],
}

async function handleRegister() {
  const valid = await regFormRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const res = await request.post('/auth/register', {
      username: regForm.value.username,
      email: regForm.value.email,
      password: regForm.value.password,
    })
    localStorage.setItem('token', res.data.token)
    localStorage.setItem('username', res.data.user.username)
    ElMessage.success('注册成功')
    router.replace('/')
  } catch {
    // 错误已在 request 拦截器中处理
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f0f2f5;
}

.login-card {
  width: 420px;
}

.login-tabs {
  --el-tabs-header-margin: 0 0 12px 0;
}
</style>
