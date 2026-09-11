<template>
  <div class="page-container">
    <!-- 页头 -->
    <div class="a24-page-header">
      <div>
        <div class="crumb">首页 / 合同管理 / <b>上传合同</b></div>
        <div class="title-row">
          <span class="icon"><el-icon><UploadFilled /></el-icon></span>
          <h3>合同上传</h3>
        </div>
        <div class="desc">上传合同文件并填写基本信息，系统将自动解析并触发 AI 审核</div>
      </div>
    </div>

    <!-- 步骤条 -->
    <div class="a24-steps">
      <div class="step on"><span class="dot">1</span>上传文件</div>
      <div class="line" :class="{ done: selectedFile }"></div>
      <div class="step" :class="{ on: selectedFile }"><span class="dot">2</span>填写信息</div>
      <div class="line" :class="{ done: uploading }"></div>
      <div class="step" :class="{ on: uploading }"><span class="dot">3</span>开始审核</div>
    </div>

    <!-- 左右分栏 -->
    <el-row :gutter="20">
      <!-- 左：拖拽上传区 -->
      <el-col :xs="24" :md="14">
        <el-upload
          ref="uploadRef"
          class="upload-dragger"
          drag
          action="#"
          :auto-upload="false"
          :limit="1"
          :accept="'.docx,.pdf,.jpg,.jpeg,.png,.tiff,.tif,.bmp'"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
          :file-list="fileList"
        >
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="upload-text">
            <p>将合同文件拖拽到此处，或 <em>点击选择文件</em></p>
            <p class="upload-hint">支持 .docx / .pdf / 图片(jpg/png/tiff) 格式，单文件最大 10MB</p>
          </div>
        </el-upload>
      </el-col>

      <!-- 右：合同信息表单 -->
      <el-col :xs="24" :md="10">
        <el-card shadow="hover" class="upload-card">
          <el-form
            ref="formRef"
            :model="form"
            :rules="rules"
            label-width="90px"
            class="upload-form"
          >
            <el-form-item label="合同名称" prop="name">
              <el-input
                v-model="form.name"
                placeholder="留空则使用原文件名"
                maxlength="200"
                show-word-limit
              />
            </el-form-item>

            <el-form-item label="合同类型" prop="contract_type">
              <el-select v-model="form.contract_type" placeholder="请选择合同类型" style="width: 100%;">
                <el-option label="自动识别（推荐）" value="" />
                <el-option v-for="t in CONTRACT_TYPES" :key="t" :label="t" :value="t" />
              </el-select>
              <div class="form-hint">选择"自动识别"将由 AI 自动判定合同类型</div>
            </el-form-item>

            <el-form-item label="审核模式" prop="audit_mode">
              <el-radio-group v-model="form.audit_mode">
                <el-radio value="precise">精细审核（推荐）</el-radio>
                <el-radio value="fast">快速初筛</el-radio>
              </el-radio-group>
              <div class="form-hint">精细审核（证据抽取 + 规则裁决，准确）｜快速初筛（纯规则引擎，仅作粗筛、误报较多）</div>
            </el-form-item>

            <el-form-item>
              <el-button
                type="primary"
                :loading="uploading"
                :disabled="!selectedFile"
                @click="handleUpload"
              >
                {{ uploading ? '上传中...' : '开始上传' }}
              </el-button>
              <el-button @click="handleReset">重置</el-button>
            </el-form-item>
          </el-form>

          <!-- 上传进度条 -->
          <div v-if="uploading" class="upload-progress">
            <el-progress :percentage="progress" :status="progressStatus" />
            <p class="progress-text">{{ progressText }}</p>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>


<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { uploadContract, triggerAudit } from '../api/contract.js'
import { CONTRACT_TYPES } from '../constants/contractTypes.js'

const router = useRouter()

const uploadRef = ref(null)
const formRef = ref(null)

const selectedFile = ref(null)
const fileList = ref([])

const uploading = ref(false)
const progress = ref(0)
const progressStatus = ref('')
const progressText = ref('')

const form = reactive({
  name: '',
  contract_type: '',
  audit_mode: 'precise',
})

const rules = {
  name: [
    { max: 200, message: '合同名称不超过 200 个字符', trigger: 'blur' },
  ],
}

function handleFileChange(file) {
  const maxSize = 10 * 1024 * 1024
  if (file.size > maxSize) {
    ElMessage.error('文件大小超过 10MB 限制，请压缩后重新上传')
    fileList.value = []
    selectedFile.value = null
    return
  }

  const ext = file.name.split('.').pop().toLowerCase()
  if (!['docx', 'pdf', 'jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp'].includes(ext)) {
    ElMessage.error('仅支持 .docx / .pdf / 图片(jpg/png/tiff) 格式')
    fileList.value = []
    selectedFile.value = null
    return
  }

  selectedFile.value = file.raw
  fileList.value = [file]

  if (!form.name) {
    form.name = file.name.replace(/\.(docx|pdf)$/i, '')
  }
}

function handleFileRemove() {
  selectedFile.value = null
  fileList.value = []
}

async function handleUpload() {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择合同文件')
    return
  }

  uploading.value = true
  progress.value = 0
  progressStatus.value = ''
  progressText.value = '正在上传文件...'

  try {
    const res = await uploadContract(selectedFile.value, {
      name: form.name || undefined,
      contract_type: form.contract_type || undefined,
      audit_mode: form.audit_mode,
      onProgress: (e) => {
        if (e.total) {
          const pct = Math.round((e.loaded / e.total) * 100)
          progress.value = pct
          if (e.loaded === e.total) {
            progressText.value = '文件已上传，正在解析合同文本...'
          } else {
            progressText.value = `正在上传... ${pct}%`
          }
        }
      },
    })

    progress.value = 100
    progressStatus.value = 'success'
    progressText.value = '上传成功！'

    const contractId = res.data?.id
    if (contractId) {
      ElMessage.success('合同上传成功，审核已触发')
      triggerAudit(contractId).catch(() => {
        ElMessage.warning('审核触发失败，请在合同列表页手动触发')
      })
    } else {
      ElMessage.success('合同上传成功')
    }
    router.push('/contracts')
  } catch {
    progressStatus.value = 'exception'
    progressText.value = '上传失败，请重试'
    ElMessage.error('上传失败，请检查网络连接后重试')
  } finally {
    uploading.value = false
  }
}

function handleReset() {
  uploadRef.value?.clearFiles()
  formRef.value?.resetFields()
  form.name = ''
  form.contract_type = ''
  form.audit_mode = 'precise'
  selectedFile.value = null
  progress.value = 0
  progressStatus.value = ''
  progressText.value = ''
}
</script>

<style scoped>
.page-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

/* 拖拽区：占满左栏，内容居中 */
.upload-dragger :deep(.el-upload-dragger) {
  min-height: 340px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  border: 2px dashed #C4CDE3;
  transition: all 0.18s;
}
.upload-dragger :deep(.el-upload-dragger:hover) {
  border-color: #1935C3;
  background: #F5F8FF;
}

.upload-icon {
  font-size: 48px;
  color: #1935C3;
  margin-bottom: 8px;
}

.upload-text p {
  margin: 4px 0;
  font-size: 15px;
  color: #606266;
}

.upload-text em {
  color: #1935C3;
  font-style: normal;
}

.upload-hint {
  font-size: 13px !important;
  color: #909399 !important;
}

.upload-form {
  margin-top: 4px;
}

.form-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  line-height: 1.5;
}

.upload-progress {
  margin-top: 16px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
}

.progress-text {
  text-align: center;
  font-size: 13px;
  color: #606266;
  margin-top: 8px;
}
</style>

