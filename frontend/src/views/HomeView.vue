<template>
  <div style="padding: 50px; text-align: center;">
    <h1>A24 合同智能审核系统</h1>
    <p>后端状态：{{ backendStatus }}</p>
    <el-button type="primary" @click="checkBackend">测试后端连通性</el-button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import axios from 'axios'

const backendStatus = ref('未检测')

async function checkBackend() {
  try {
    const res = await axios.get('/api/health')
    backendStatus.value = '✅ 后端连通！' + JSON.stringify(res.data)
  } catch (e) {
    backendStatus.value = '❌ 后端未启动，请确认 uvicorn 在运行'
  }
}
</script>
