<template>
  <div style="padding: 20px;">
    <h3>审核结果</h3>
    <el-divider />

    <!-- 统计卡片 -->
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="风险总数" :value="15" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="高风险" :value="3">
            <template #suffix>
              <span style="color: #F56C6C; font-size: 14px;">条</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="中风险" :value="7">
            <template #suffix>
              <span style="color: #E6A23C; font-size: 14px;">条</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="低风险" :value="5">
            <template #suffix>
              <span style="color: #67C23A; font-size: 14px;">条</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
    </el-row>

    <!-- 风险列表 -->
    <el-card shadow="hover" style="margin-top: 20px;">
      <template #header>
        <span>风险列表</span>
      </template>
      <el-table :data="riskList" stripe border style="width: 100%;">
        <el-table-column prop="type" label="风险类型" width="150" />
        <el-table-column prop="level" label="等级" width="100">
          <template #default="{ row }">
            <el-tag
              :type="row.level === 'high' ? 'danger' : row.level === 'medium' ? 'warning' : 'success'"
            >
              {{ row.level === 'high' ? '高风险' : row.level === 'medium' ? '中风险' : '低风险' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="clause" label="原文片段" min-width="250" show-overflow-tooltip />
        <el-table-column prop="reason" label="判定理由" min-width="250" show-overflow-tooltip />
        <el-table-column prop="confidence" label="置信度" width="120">
          <template #default="{ row }">
            <el-progress :percentage="row.confidence" :stroke-width="8" />
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const riskList = ref([
  { type: 'R01 违约金过高', level: 'high', clause: '若乙方违约，需支付合同总金额的 50% 作为违约金。', reason: '违约金比例 50% 超过法定上限 30%', confidence: 92 },
  { type: 'R02 无限责任', level: 'high', clause: '乙方需赔偿甲方因此遭受的全部损失。', reason: '"全部损失"属于无限责任表述，应设赔偿上限', confidence: 88 },
  { type: 'R03 单方解约权', level: 'high', clause: '甲方有权随时单方面解除本合同。', reason: '"随时单方面解除"剥夺乙方合同稳定性', confidence: 95 },
  { type: 'R04 管辖条款缺失', level: 'medium', clause: '（未找到管辖条款）', reason: '合同未约定争议管辖法院或仲裁机构', confidence: 85 },
  { type: 'R05 付款条件不明确', level: 'medium', clause: '甲方在验收合格后支付相应款项。', reason: '"相应款项"未明确金额或比例，存在支付争议风险', confidence: 78 },
  { type: 'R06 知识产权归属', level: 'medium', clause: '项目成果的知识产权由双方协商确定。', reason: '知识产权归属未明确，应在签约前确定', confidence: 80 },
  { type: 'R07 保密条款过宽', level: 'medium', clause: '乙方不得向任何第三方透露与本合同相关的任何信息。', reason: '保密范围"任何信息"过于宽泛，应限定为"商业秘密"', confidence: 75 },
  { type: 'R08 验收标准缺失', level: 'medium', clause: '成果需达到甲方要求的标准。', reason: '验收标准由甲方单方决定，缺乏客观依据', confidence: 82 },
  { type: 'R09 不可抗力条款', level: 'low', clause: '因不可抗力导致无法履行的，双方免责。', reason: '不可抗力定义范围过窄，建议补充具体情形', confidence: 70 },
  { type: 'R10 合同期限', level: 'low', clause: '本合同有效期至项目完成。', reason: '"项目完成"缺乏明确的时间节点', confidence: 65 },
  { type: 'R11 续约条款', level: 'low', clause: '（未找到续约条款）', reason: '长期合作合同建议加入续约机制', confidence: 60 },
  { type: 'R12 通知送达', level: 'low', clause: '双方通过书面方式通知对方。', reason: '"书面方式"未明确包含电子邮件，建议补充', confidence: 55 },
])
</script>
