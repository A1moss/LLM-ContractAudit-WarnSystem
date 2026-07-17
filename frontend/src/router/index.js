import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/HomeView.vue')
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/LoginView.vue')
  },
  {
    path: '/contracts/upload',
    name: 'ContractUpload',
    component: () => import('../views/ContractUpload.vue')
  },
  {
    path: '/contracts',
    name: 'ContractList',
    component: () => import('../views/ContractList.vue')
  },
  {
    path: '/contracts/:id',
    name: 'ContractDetail',
    component: () => import('../views/ContractDetail.vue')
  },
  {
    path: '/audit/result',
    name: 'AuditResult',
    component: () => import('../views/AuditResult.vue')
  },
  {
    path: '/audit/result/:contractId',
    name: 'AuditResultDetail',
    component: () => import('../views/AuditResultDetail.vue')
  },
  {
    path: '/audit/report',
    name: 'AuditReport',
    component: () => import('../views/AuditReport.vue')
  },
  {
    path: '/audit/report/:contractId',
    name: 'AuditReportDetail',
    component: () => import('../views/AuditReportDetail.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
