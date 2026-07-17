<template>
  <div id="app">
    <!-- 顶部导航栏 — 登录页不显示 -->
    <el-menu
      v-if="showNav"
      mode="horizontal"
      :default-active="activeMenu"
      :default-openeds="openSubs"
      :ellipsis="false"
      class="app-nav"
      router
    >
      <!-- 品牌名 -->
      <div class="nav-brand" @click="$router.push('/')">
        <el-icon><Document /></el-icon>
        <span>A24 合同审核系统</span>
      </div>

      <el-menu-item index="/">
        <el-icon><HomeFilled /></el-icon>
        首页
      </el-menu-item>

      <el-sub-menu index="contracts-sub">
        <template #title>
          <el-icon><FolderOpened /></el-icon>
          合同管理
        </template>
        <el-menu-item index="/contracts">合同列表</el-menu-item>
        <el-menu-item index="/contracts/upload">上传合同</el-menu-item>
      </el-sub-menu>

      <el-sub-menu index="audit-sub">
        <template #title>
          <el-icon><Checked /></el-icon>
          审核中心
        </template>
        <el-menu-item index="/audit/result">审核结果</el-menu-item>
        <el-menu-item index="/audit/report">审核报告</el-menu-item>
      </el-sub-menu>

      <!-- 右侧占位 -->
      <div class="nav-spacer" />

      <!-- 登录状态：个人中心下拉 -->
      <el-sub-menu v-if="isLoggedIn" index="user-sub" popper-class="user-popper">
        <template #title>
          <el-icon><User /></el-icon>
          <span>{{ username }}</span>
        </template>
        <el-menu-item index="profile" disabled>
          <el-icon><User /></el-icon>
          个人信息（开发中）
        </el-menu-item>
        <el-menu-item index="logout" divided @click="handleLogout">
          <el-icon><SwitchButton /></el-icon>
          退出登录
        </el-menu-item>
      </el-sub-menu>

      <!-- 未登录状态：登录按钮 -->
      <el-menu-item v-else index="/login" class="login-menu-item">
        <el-icon><User /></el-icon>
        登录
      </el-menu-item>
    </el-menu>

    <!-- 返回按钮 — 首页和登录页不显示 -->
    <div v-if="showNav && showBack" class="back-bar">
      <el-button text @click="goBack" class="back-btn">
        <el-icon><ArrowLeft /></el-icon>
        返回
      </el-button>
    </div>

    <!-- 页面内容 -->
    <router-view />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  HomeFilled, FolderOpened, Checked, User,
  SwitchButton, ArrowLeft, Document,
} from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()

// ── 是否显示导航栏（登录页隐藏）──
const showNav = computed(() => route.path !== '/login')

// ── 是否显示返回按钮（首页和登录页隐藏）──
const showBack = computed(() => route.path !== '/' && route.path !== '/login')

// ── 当前高亮菜单项 ──
const activeMenu = computed(() => {
  const p = route.path
  if (p.startsWith('/contracts')) return p
  if (p.startsWith('/audit')) return p
  return p
})

// ── 子菜单默认展开（匹配当前路由所属分类）──
const openSubs = computed(() => {
  const subs = []
  if (route.path.startsWith('/contracts')) subs.push('contracts-sub')
  if (route.path.startsWith('/audit')) subs.push('audit-sub')
  return subs
})

// ── 登录状态 ──
const isLoggedIn = computed(() => !!localStorage.getItem('token'))

// ── 用户名 ──
const username = computed(() => {
  return localStorage.getItem('username') || '未登录'
})

// ── 返回 ──
function goBack() {
  // 如果历史记录为空，跳到首页
  if (window.history.length <= 2) {
    router.push('/')
  } else {
    router.back()
  }
}

// ── 退出登录 ──
function handleLogout() {
  localStorage.removeItem('token')
  localStorage.removeItem('username')
  router.push('/login')
}
</script>

<style>
/* ── 全局样式 ── */
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: #f0f2f5;
  min-height: 100vh;
}
</style>

<style scoped>
/* ── 导航栏 ── */
.app-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  height: 56px;
  display: flex;
  align-items: center;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  padding: 0 20px;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-right: 24px;
  font-size: 17px;
  font-weight: 600;
  color: #303133;
  cursor: pointer;
  user-select: none;
}

.nav-brand:hover {
  color: #409EFF;
}

.nav-spacer {
  flex: 1;
}

/* ── 返回按钮栏 ── */
.back-bar {
  padding: 8px 20px;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
}

.back-btn {
  font-size: 14px;
  color: #606266;
}

.back-btn:hover {
  color: #409EFF;
}

/* ── 用户下拉弹窗 ── */
:deep(.user-popper) {
  min-width: 160px !important;
}
</style>
