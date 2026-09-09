<template>
  <div class="home">
    <!-- hero -->
    <section class="hero">
      <div class="hero-inner">
        <div class="hero-left">
          <span class="kicker">欢迎回来，{{ username }}</span>
          <h1>A24 合同智能审核系统</h1>
          <p class="sub">面向企业法务与风控的 AI 原生合同审核平台，实现「上传 → 分类 → 要素抽取 → 风险识别 → 修改建议 → 审核报告」全流程闭环。</p>
          <div class="actions">
            <div class="btn btn-primary" @click="router.push('/contracts/upload')">上传合同</div>
            <div class="btn btn-ghost" @click="router.push('/contracts')">我的合同</div>
          </div>
        </div>
        <div class="hero-right">
          <img src="/images/hero.png" alt="A24 平台示意" />
        </div>
      </div>
    </section>

    <!-- 核心数据条（实时，来自后端 /stats/dashboard，每 30s 自动刷新） -->
    <section class="metrics">
      <div class="metrics-inner">
        <div class="metric">
          <div class="num">{{ stats.today_audit }}<small>份</small></div>
          <div class="lbl">今日审核</div>
        </div>
        <div class="metric">
          <div class="num">{{ stats.pending }}<small>份</small></div>
          <div class="lbl">待处理</div>
        </div>
        <div class="metric">
          <div class="num">{{ stats.month_risks }}<small>条</small></div>
          <div class="lbl">本月风险</div>
        </div>
        <div class="metric">
          <div class="num">{{ stats.approval_rate }}<small>%</small></div>
          <div class="lbl">通过率</div>
        </div>
      </div>
    </section>

    <!-- 核心能力 -->
    <section class="capabilities" id="capabilities">
      <div class="wrap">
        <div class="section-head">
          <h2>核心能力</h2>
          <div class="en">Core Capabilities</div>
          <div class="line"></div>
        </div>
        <div v-for="(c, i) in capabilities" :key="c.no" class="feat-row" :class="{ reverse: i % 2 === 1 }">
          <div class="feat-img"><img :src="c.img" :alt="c.title" /></div>
          <div class="feat-txt">
            <span class="no">{{ c.no }}</span>
            <h3>{{ c.title }}</h3>
            <p class="desc">{{ c.desc }}</p>
            <span class="more" @click="router.push(c.link)">{{ c.action }} →</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 最近合同 -->
    <section class="recent">
      <div class="wrap">
        <div class="section-head">
          <h2>最近合同</h2>
          <div class="line"></div>
        </div>
        <div class="table-card" v-loading="loading">
          <el-table :data="recentContracts" stripe>
            <template #empty><el-empty description="暂无合同" /></template>
            <el-table-column prop="file_name" label="文件名" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link type="primary" @click="router.push(`/contracts/${row.id}`)">{{ row.file_name }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="contract_type" label="类型" width="130" />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="风险等级" width="100">
              <template #default="{ row }">
                <el-tag v-if="row.risk_level" :type="riskTag(row.risk_level)" size="small">{{ riskLabel(row.risk_level) }}</el-tag>
                <span v-else style="color: #8A93A6;">—</span>
              </template>
            </el-table-column>
            <el-table-column label="上传时间" width="160">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>

    <!-- 页脚 -->
    <footer class="footer">
      <div class="wrap">
        <div class="f-top">
          <div class="f-brand">
            <div class="f-logo">A</div>
            <span class="f-name">A24 合同审核系统</span>
          </div>
          <div class="f-links">
            <span @click="router.push('/contracts')">合同管理</span>
            <span @click="router.push('/audit/result')">审核中心</span>
            <span @click="router.push('/templates')">模板管理</span>
          </div>
        </div>
        <div class="f-bottom">© 2026 A24 · 海底汪汪队 —— 命题企业：网新恒天</div>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import request from '../utils/request.js'
import { formatTime } from '../utils/format.js'

const router = useRouter()
const username = ref(localStorage.getItem('username') || '用户')
const loading = ref(false)

const stats = reactive({
  today_audit: 0,
  pending: 0,
  month_risks: 0,
  approval_rate: 0,
})
const recentContracts = ref([])

// 核心能力（描述不含固定数字，数据由后端实时提供；跳转到明确的功能页面）
const capabilities = [
  { no: '01', title: '智能合同分类', desc: '基于 RAG 检索范本 + 大模型推理，将合同自动归类到民法典 11 类法理分类，并叠加「服务外包」等业务标签。', img: '/images/feat-1.png', link: '/contracts', action: '进入合同列表' },
  { no: '02', title: '风险识别与预警', desc: '「LLM 只抽事实证据 + 确定性规则裁决」架构，覆盖 R01–R13 风险规则，风险判定不做 LLM 自由发挥，确保判定可解释、可溯源。', img: '/images/feat-2.png', link: '/audit/result', action: '查看审核历史' },
  { no: '03', title: '合同要素抽取', desc: '自动抽取合同关键要素（主体、金额、期限、违约责任等），为审核与比对提供结构化底座。', img: '/images/feat-3.png', link: '/contracts/upload', action: '去上传合同' },
  { no: '04', title: '审核报告生成', desc: '一键生成结构化审核报告：风险清单、修改建议、条款比对与风险热力图，支持反馈标注闭环，人机协同完成最终审核。', img: '/images/feat-4.png', link: '/audit/report', action: '查看审核报告' },
]

function statusLabel(s) {
  const map = { uploaded: '已上传', parsed: '已解析', auditing: '审核中', completed: '审核完成', reviewed: '待验收', approved: '已验收' }
  return map[s] || s || '未知'
}
function statusTag(s) {
  const map = { completed: 'success', approved: 'success', reviewed: 'primary', auditing: 'warning', parsed: 'info', uploaded: 'info' }
  return map[s] || 'info'
}
function riskLabel(level) {
  const map = { high: '高风险', medium: '中风险', low: '低风险' }
  return map[level] || level
}
function riskTag(level) {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  if (level === 'low') return 'success'
  return 'info'
}

// 拉取仪表盘数据（isRefresh=true 时为定时静默刷新，不触发 loading）
async function fetchDashboard(isRefresh = false) {
  if (!isRefresh) loading.value = true
  try {
    const res = await request.get('/stats/dashboard')
    const d = res.data || {}
    stats.today_audit = d.today_audit ?? 0
    stats.pending = d.pending ?? 0
    stats.month_risks = d.month_risks ?? 0
    stats.approval_rate = d.approval_rate ?? 0
    recentContracts.value = d.recent_contracts || []
  } catch {
    // 后端未启动时保持现值，不打断页面
  } finally {
    if (!isRefresh) loading.value = false
  }
}

let refreshTimer = null

onMounted(() => {
  username.value = localStorage.getItem('username') || '用户'
  fetchDashboard()
  // 每 30 秒静默刷新一次，保证数据实时
  refreshTimer = setInterval(() => fetchDashboard(true), 30000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = null
})
</script>

<style scoped>
.home { background: #fff; }

/* ── hero ── */
.hero {
  position: relative; overflow: hidden;
  background:
    radial-gradient(1100px 420px at 82% -10%, rgba(27,112,240,.38) 0%, transparent 60%),
    radial-gradient(800px 480px at -8% 118%, rgba(25,53,195,.55) 0%, transparent 55%),
    linear-gradient(118deg, #081426 0%, #0D2447 48%, #102B5E 100%);
  padding: 64px 40px 80px; color: #fff;
}
.hero::before {
  content: ""; position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px);
  background-size: 52px 52px;
  mask-image: radial-gradient(ellipse at 70% 40%, #000 20%, transparent 72%);
  -webkit-mask-image: radial-gradient(ellipse at 70% 40%, #000 20%, transparent 72%);
}
.hero-inner {
  position: relative; z-index: 2; display: flex; align-items: center; gap: 48px;
  max-width: 1200px; margin: 0 auto;
}
.hero-left { flex: 1.1; }
.kicker {
  display: inline-block; font-size: 13px; letter-spacing: 1px; color: #8FB4FF;
  border: 1px solid rgba(143,180,255,.4); padding: 4px 14px; border-radius: 999px; margin-bottom: 18px;
}
.hero h1 { font-size: 40px; font-weight: 700; line-height: 1.25; letter-spacing: .5px; color: #fff; margin: 0; }
.hero .sub { margin-top: 16px; font-size: 16px; color: #B9C6E4; max-width: 520px; }
.actions { margin-top: 30px; display: flex; gap: 16px; }
.btn { display: inline-flex; align-items: center; gap: 7px; padding: 12px 28px; border-radius: 6px; font-size: 15px; cursor: pointer; transition: all .15s; border: 1px solid transparent; user-select: none; }
.btn-primary { background: #1B70F0; color: #fff; }
.btn-primary:hover { background: #3D87F5; }
.btn-ghost { background: transparent; color: #DCE6F7; border-color: rgba(255,255,255,.35); }
.btn-ghost:hover { background: rgba(255,255,255,.1); color: #fff; }
.hero-right { flex: .9; }
.hero-right img { width: 100%; border-radius: 12px; display: block; border: 1px solid rgba(255,255,255,.15); box-shadow: 0 20px 50px rgba(3,12,30,.5); }

/* ── 数据条 ── */
.metrics { background: #fff; border-bottom: 1px solid #EDF0F5; }
.metrics-inner { max-width: 1200px; margin: 0 auto; display: grid; grid-template-columns: repeat(4, 1fr); padding: 40px 0; }
.metric { text-align: center; position: relative; }
.metric + .metric::before { content: ""; position: absolute; left: 0; top: 12%; bottom: 12%; width: 1px; background: #EDF0F5; }
.metric .num { font-size: 36px; font-weight: 700; color: #1935C3; line-height: 1.1; }
.metric .num small { font-size: 18px; color: #1935C3; margin-left: 2px; }
.metric .lbl { margin-top: 8px; font-size: 14px; color: #8A93A6; }

/* ── 区块标题 ── */
.wrap { max-width: 1200px; margin: 0 auto; padding: 0 40px; }
.section-head { text-align: center; padding: 64px 0 8px; }
.section-head h2 { font-size: 30px; font-weight: 700; color: #131313; margin: 0; }
.section-head .en { font-size: 13px; color: #8A93A6; letter-spacing: 2px; margin-top: 6px; text-transform: uppercase; }
.section-head .line { width: 44px; height: 3px; background: #1935C3; margin: 16px auto 0; border-radius: 2px; }

/* ── 功能模块 ── */
.capabilities { padding-bottom: 30px; }
.feat-row { display: flex; align-items: center; gap: 60px; padding: 34px 0; }
.feat-row.reverse { flex-direction: row-reverse; }
.feat-img { flex: 1; }
.feat-img img { width: 100%; border-radius: 10px; display: block; box-shadow: 0 10px 30px rgba(13,36,71,.12); }
.feat-txt { flex: 1; position: relative; padding-left: 20px; }
.feat-row.reverse .feat-txt { padding-left: 0; padding-right: 20px; }
.feat-txt .no { position: absolute; left: -10px; top: -42px; font-size: 84px; font-weight: 700; color: #EFF3FB; line-height: 1; z-index: 0; user-select: none; }
.feat-row.reverse .feat-txt .no { left: auto; right: -6px; }
.feat-txt h3 { font-size: 24px; font-weight: 700; color: #131313; margin: 0; position: relative; z-index: 1; }
.feat-txt .desc { margin-top: 14px; font-size: 14.5px; color: #585E7D; line-height: 1.9; position: relative; z-index: 1; }
.more { display: inline-flex; align-items: center; gap: 6px; margin-top: 20px; color: #1935C3; font-size: 14px; cursor: pointer; position: relative; z-index: 1; }
.more:hover { color: #1B70F0; }

/* ── 最近合同 ── */
.recent { padding-bottom: 50px; }
.table-card { background: #fff; border: 1px solid #E5EAF2; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(13,36,71,.04); }
.table-card :deep(.el-table th.el-table__cell) { background: #F5F8FC; color: #585E7D; font-weight: 600; }
.table-card :deep(.el-table tr) { transition: background .12s; }

/* ── 页脚 ── */
.footer { background: #081426; color: #9FB0C8; padding: 44px 0 30px; margin-top: 40px; }
.f-top { display: flex; justify-content: space-between; align-items: center; padding-bottom: 24px; border-bottom: 1px solid rgba(255,255,255,.08); }
.f-brand { display: flex; align-items: center; gap: 10px; }
.f-logo { width: 30px; height: 30px; border-radius: 7px; background: linear-gradient(135deg,#1935C3,#1B70F0); color: #fff; font-weight: 700; display: flex; align-items: center; justify-content: center; font-size: 15px; }
.f-name { color: #fff; font-size: 16px; font-weight: 600; }
.f-links { display: flex; gap: 30px; font-size: 14px; }
.f-links span { cursor: pointer; }
.f-links span:hover { color: #fff; }
.f-bottom { padding-top: 20px; font-size: 12.5px; color: #6E7A95; text-align: center; }
</style>
