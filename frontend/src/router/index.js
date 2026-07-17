import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/HomeView.vue'),
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/LoginView.vue'),
  },
  {
    path: '/contracts/upload',
    name: 'ContractUpload',
    component: () => import('../views/ContractUpload.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/contracts',
    name: 'ContractList',
    component: () => import('../views/ContractList.vue'),
  },
  {
    path: '/contracts/:id',
    name: 'ContractDetail',
    component: () => import('../views/ContractDetail.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/audit/result',
    name: 'AuditResult',
    component: () => import('../views/AuditResult.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/audit/result/:contractId',
    name: 'AuditResultDetail',
    component: () => import('../views/AuditResultDetail.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/audit/report',
    name: 'AuditReport',
    component: () => import('../views/AuditReport.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/audit/report/:contractId',
    name: 'AuditReportDetail',
    component: () => import('../views/AuditReportDetail.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('../views/NotFoundView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ── 路由守卫：游客模式 / 登录拦截 ──
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  const guestMode = localStorage.getItem('guest_mode') !== 'false' // 默认开启

  // 已登录 → 放行
  if (token) {
    // 已登录时访问 /login → 跳到首页
    if (to.path === '/login') {
      return next('/')
    }
    return next()
  }

  // 未登录 + 游客模式开启 → 放行（仅限无需认证的页面）
  if (guestMode) {
    if (to.meta.requiresAuth) {
      return next('/login')
    }
    return next()
  }

  // 未登录 + 游客模式关闭 → 全部跳登录
  if (to.path !== '/login') {
    return next('/login')
  }

  next()
})

export default router
