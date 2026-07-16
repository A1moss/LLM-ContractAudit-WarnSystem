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
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
