<template>
  <div class="wb">
    <!-- 顶栏：会话标题 + 轮数（不再放"下载修订版 DOCX"和开发术语） -->
    <div class="wb-top">
      <div class="wb-top-l">
        <span class="wb-session-title">{{ centerTitle }}</span>
        <el-tag v-if="s && !isOverview" size="small" :type="stateTag" effect="light">
          {{ sessionState ? sessionState.label : '待处理' }}
        </el-tag>
        <span v-if="s?.rounds" class="wb-rounds">共 {{ s.rounds }} 轮</span>
      </div>
      <div class="wb-top-r">
        <el-button size="small" text @click="reloadAll">刷新修改记录</el-button>
      </div>
    </div>

    <div class="wb-body">
      <!-- ══════════ 左栏：会话导航（~240px，独立滚动） ══════════ -->
      <aside class="wb-left">
        <div class="wb-stats">
          <div class="wb-stat"><b>{{ ws.riskItems.length }}</b><span>待处理风险</span></div>
          <div class="wb-stat"><b>{{ ws.comparisonIssues.length }}</b><span>条款比对问题</span></div>
          <div class="wb-stat"><b>{{ ws.confirmedCount || 0 }}</b><span>已确认修改</span></div>
        </div>
        <div class="wb-stats-note">
          「已确认修改」只统计你在专项会话里最终确认、且修改位置已可靠确定的具体修改。
        </div>

        <div v-if="ws.revisionsError" class="wb-left-err">{{ ws.revisionsError }}</div>

        <div class="wb-groups">
          <template v-for="g in SESSION_GROUPS" :key="g.key">
            <div class="wb-group-title">
              {{ g.label }}<span class="n">{{ (ws.sessionsByGroup[g.key] || []).length }}</span>
            </div>

            <div v-if="!(ws.sessionsByGroup[g.key] || []).length" class="wb-group-empty">
              <template v-if="g.key === 'overview'">—</template>
              <template v-else-if="g.key === 'add'">缺失的条款会出现在这里，可用「新增条款」起草。</template>
              <template v-else-if="g.key === 'history'">还没有历史修改记录。</template>
              <template v-else>这份合同暂时没有需要修改的风险点。</template>
            </div>

            <div
              v-for="item in ws.sessionsByGroup[g.key] || []"
              :key="item.key"
              class="wb-item"
              :class="{ 'is-active': item.key === ws.activeKey }"
              @click="ws.selectSession(item.key)"
            >
              <div class="wb-item-top">
                <el-tag size="small" :type="groupTag(item.group)" effect="plain">{{ groupShort(item.group) }}</el-tag>
                <span class="wb-item-title">{{ groupTitle(item) }}</span>
                <span v-if="item.rounds" class="wb-item-rounds">{{ item.rounds }} 轮</span>
              </div>
              <div class="wb-item-sub">{{ groupSubtitle(item) }}</div>
              <div class="wb-item-locate">
                <span class="dot" :class="'dot-' + itemDot(item)" />
                <span class="txt">{{ itemStatusText(item) }}</span>
              </div>
            </div>
          </template>
        </div>
      </aside>

      <!-- ══════════ 中栏：当前会话（对话区滚动，输入框固定底部） ══════════ -->
      <section class="wb-center">
        <template v-if="!s">
          <el-empty description="请选择左侧会话，或从「风险详情 / 条款比对」进入修改" />
        </template>
        <template v-else>
          <div class="wb-ctx">
            <div class="wb-ctx-row">
              <span class="k">正在处理</span>
              <span class="v">{{ centerTitle }}</span>
              <el-tag v-if="clauseNoLabel" size="small" effect="plain">{{ clauseNoLabel }}</el-tag>
              <span class="wb-spacer" />
              <el-tag v-if="sessionState" size="small" :type="stateTag" effect="light">{{ sessionState.label }}</el-tag>
            </div>
            <div v-if="!isOverview" class="wb-ctx-orig">
              <span class="k">修改对象原文</span>
              <span class="v" :class="{ muted: !s.clauseText }">
                {{ s.clauseText ? ws.clipText(s.clauseText, 220) : '暂未确定改哪一段，请在右侧「修改位置」中确认' }}
              </span>
            </div>
          </div>

          <el-alert
            v-if="isOverview" type="info" show-icon :closable="false" class="wb-overview-note"
            title="总体会话负责整份合同的统筹修改"
          >
            <template #default>
              它可以生成和管理具体修改方案；具体修改仍需经过可靠定位和你在专项会话里的最终确认，
              才会写入修改后的合同。这里不会用整份新合同覆盖原文件。
            </template>
          </el-alert>

          <div class="wb-chat">
            <div v-if="!s.rounds && !uiFor.pending" class="wb-empty">
              <template v-if="isOverview">输入你对整份合同的修改要求，AI 会结合当前修改情况给出综合方案。</template>
              <template v-else-if="isAddSession">这条款在合同里不存在，需要新增：描述你希望新增的内容，插入位置在右侧工作区确认。</template>
              <template v-else-if="!s.clauseText">暂未确定改哪一段：请在右侧「修改位置」中确认后再描述修改要求。</template>
              <template v-else>描述你对这条款的修改要求，AI 会帮你起草修改建议。</template>
            </div>

            <div v-for="(r, i) in s.revs" :key="r.id" class="wb-round">
              <div class="wb-round-head">
                <span class="wb-round-no">第 {{ i + 1 }} 轮</span>
                <el-tag size="small" effect="plain">{{ r.operation === 'add_clause' ? '新增条款' : '条款修改' }}</el-tag>
                <span v-if="r.clause_no" class="wb-muted">{{ clauseNoText(r.clause_no) }}</span>
                <span class="wb-muted">{{ fmtTime(r.created_at) }}</span>
                <!-- 采用态来自后端 /revisions 的 adopted 字段（刷新后仍能恢复） -->
                <el-tag v-if="r.adopted === true" size="small" type="success" effect="dark">已采用</el-tag>
              </div>

              <div class="wb-user-card">
                <div class="wb-card-label">你的要求</div>
                <div class="wb-user-text">{{ displayInstruction(r.instruction) }}</div>
              </div>

              <div class="wb-ai-card">
                <div class="wb-card-label">AI 修改建议</div>
                <pre class="wb-ai-text">{{ r.revised_clause || '（空）' }}</pre>
                <div v-if="r.explanation" class="wb-ai-exp">说明：{{ r.explanation }}</div>
                <div class="wb-chips">
                  <el-tag v-for="(c, ci) in r.constraints || []" :key="'c' + ci" size="small" effect="plain" type="info">约束：{{ c }}</el-tag>
                  <el-tag v-for="(lb, li) in r.legal_basis || []" :key="'l' + li" size="small" effect="plain" type="warning">依据：{{ lb }}</el-tag>
                </div>
                <div v-if="r.operation === 'add_clause' && r.position" class="wb-ai-pos">
                  插入位置：{{ ws.positionText(r.position) }}
                </div>
                <div v-if="r.remaining_risks?.length" class="wb-ai-risk">
                  ⚠ 剩余风险：{{ r.remaining_risks.join('；') }}
                </div>
                <div class="wb-ai-actions">
                  <el-button size="small" text @click="viewRound(i)">查看此版对比</el-button>
                  <el-button size="small" text @click="copyText(r.revised_clause)">复制修改稿</el-button>
                </div>
              </div>
            </div>

            <div v-if="uiFor.pending" class="wb-round">
              <div class="wb-round-head"><span class="wb-round-no">第 {{ s.rounds + 1 }} 轮</span><span class="wb-muted">进行中</span></div>
              <div class="wb-user-card"><div class="wb-card-label">你的要求</div><div class="wb-user-text">{{ uiFor.pending.instruction }}</div></div>
              <div class="wb-ai-card">
                <div class="wb-card-label">AI 正在起草修改建议…</div>
                <el-skeleton :rows="3" animated />
              </div>
            </div>
          </div>

          <div class="wb-input">
            <div v-if="isAddSession && s.rounds" class="wb-refine-tip">
              正在修改「新增条款」（插入位置：{{ ws.positionText(currentPosition) || '尚未选定' }}）；
              同一位置只保留最新版本，右侧始终显示当前版本。
            </div>
            <div v-if="!isOverview && !isAddSession && !s.clauseText" class="wb-refine-tip warn">
              暂未确定改哪一段：请先在右侧「修改位置」中确认，否则无法安全写入修改后的合同。
            </div>
            <div class="wb-chips-input">
              <el-tag
                v-for="c in quickChips" :key="c" size="small" effect="plain" class="wb-chip"
                @click="appendChip(c)"
              >{{ c }}</el-tag>
            </div>
            <div class="wb-input-row">
              <el-input
                v-model="uiFor.input"
                type="textarea"
                :rows="2"
                :placeholder="placeholder"
                @keydown.enter.exact.prevent="onSend"
              />
              <el-button type="primary" :loading="!!uiFor.pending" @click="onSend">发送</el-button>
            </div>
          </div>
        </template>
      </section>

      <!-- ══════════ 右栏：合同工作区（~420px，独立滚动） ══════════ -->
      <aside class="wb-right">
        <!-- 总体会话 → 总控台 -->
        <OverviewPlanPanel v-if="isOverview" :ws="ws" />

        <template v-else-if="s">
          <!-- ⑥ 历史修改：只读工作区（无采纳/继续调整/重新定位/确认新增） -->
          <template v-if="isHistory">
            <div class="wb-card">
              <div class="wb-card-t">
                历史修改
                <el-tag size="small" type="info" effect="light">只读</el-tag>
              </div>
              <div class="wb-kv"><span class="k">条款</span><span class="v">{{ locatedLabel }}</span></div>
              <div class="wb-kv"><span class="k">时间</span><span class="v">{{ fmtTime(s.lastRev?.created_at) || '—' }}</span></div>
              <div class="wb-muted">
                这是已保存的历史修改记录，仅供查看；需要改动请回到对应的风险或条款比对里重新处理。
              </div>
            </div>

            <div class="wb-card">
              <div class="wb-card-t">{{ diffTitle }}</div>
              <ClauseDiffView
                :before="diffBefore"
                :after="diffAfter"
                :reference="referenceText"
                :before-label="diffBeforeLabel"
                :after-label="diffAfterLabel"
              />
              <div v-if="s.lastRev?.explanation" class="wb-exp">修改说明：{{ s.lastRev.explanation }}</div>
              <div v-else class="wb-muted">这条记录没有保存修改说明。</div>
            </div>

            <div class="wb-card">
              <div class="wb-card-t">法律依据</div>
              <div v-if="s.lastRev?.legal_basis?.length" class="wb-chips">
                <el-tag v-for="(lb, i) in s.lastRev.legal_basis" :key="i" size="small" effect="plain" type="warning">{{ lb }}</el-tag>
              </div>
              <div v-else class="wb-muted">这条记录没有保存法律依据。</div>
            </div>

            <div class="wb-card">
              <div class="wb-card-t">剩余风险</div>
              <ul v-if="s.lastRev?.remaining_risks?.length" class="wb-list">
                <li v-for="(r, i) in s.lastRev.remaining_risks" :key="i">{{ r }}</li>
              </ul>
              <div v-else class="wb-muted">这条记录没有报告剩余风险。</div>
            </div>
          </template>

          <!-- ① 风险/比对 · 已定位：当前条款（原文 ↔ 修改后 + 依据 + 剩余风险 + 操作） -->
          <template v-else-if="!isAddSession && located">
            <div class="wb-card">
              <div class="wb-card-t">
                当前位置
                <el-tag size="small" :type="locationState?.tone || 'success'" effect="light">
                  {{ locationState?.label || '已确认定位' }}
                </el-tag>
              </div>
              <div class="wb-kv"><span class="k">条款</span><span class="v">{{ locatedLabel }}</span></div>
              <div class="wb-kv"><span class="k">位置来源</span><span class="v">{{ locatedSourceText }}</span></div>
              <div class="wb-actions">
                <el-button size="small" text @click="ws.startRelocate(s)">重新指定位置</el-button>
              </div>
            </div>

            <div class="wb-card">
              <div class="wb-card-t">{{ diffTitle }}</div>
              <ClauseDiffView
                :before="diffBefore"
                :after="diffAfter"
                :reference="referenceText"
                :before-label="diffBeforeLabel"
                :after-label="diffAfterLabel"
              />
              <div v-if="s.lastRev?.explanation" class="wb-exp">修改理由：{{ s.lastRev.explanation }}</div>
            </div>

            <div class="wb-card">
              <div class="wb-card-t">法律依据</div>
              <div v-if="s.lastRev?.legal_basis?.length" class="wb-chips">
                <el-tag v-for="(lb, i) in s.lastRev.legal_basis" :key="i" size="small" effect="plain" type="warning">{{ lb }}</el-tag>
              </div>
              <div v-else-if="s.risk?.legal_basis" class="wb-muted">{{ s.risk.legal_basis }}</div>
              <div v-else class="wb-muted">暂无法律依据。</div>
            </div>

            <div class="wb-card">
              <div class="wb-card-t">剩余风险</div>
              <ul v-if="s.lastRev?.remaining_risks?.length" class="wb-list">
                <li v-for="(r, i) in s.lastRev.remaining_risks" :key="i">{{ r }}</li>
              </ul>
              <div v-else class="wb-muted">
                {{ s.rounds ? '最新一轮未报告剩余风险。' : '尚无修改记录。' }}
              </div>
            </div>

            <div class="wb-card">
              <div class="wb-card-t">
                操作
                <el-tag v-if="shownRevIsAdopted" size="small" type="success" effect="light">✓ 已采用</el-tag>
              </div>
              <div class="wb-actions">
                <el-button
                  type="primary"
                  :loading="ws.adopting"
                  :disabled="!s.lastRev || shownRevIsAdopted"
                  @click="ws.confirmAdopt(s, currentRev)"
                >确认采用此版</el-button>
                <el-button @click="focusChatInput">继续调整</el-button>
              </div>
              <div class="wb-muted">
                确认采用后，下载的合同文件会使用这一版。
                继续调整生成的新版本不会自动取代它，需再次确认采用。
              </div>
            </div>

            <!-- 比对的参考条款/偏离说明（有真实参考条款才显示中间栏，已在 ClauseDiffView 内处理） -->
            <div v-if="s.cmp" class="wb-card">
              <div class="wb-card-t">条款比对说明</div>
              <div class="wb-kv"><span class="k">状态</span><span class="v">{{ cmpStatusLabel }}</span></div>
              <div v-if="s.cmp.deviation" class="wb-kv"><span class="k">偏离说明</span><span class="v">{{ s.cmp.deviation }}</span></div>
              <div v-if="s.cmp.completion" class="wb-kv"><span class="k">补全建议</span><span class="v">{{ s.cmp.completion }}</span></div>
              <div v-if="s.cmp.risk" class="wb-kv"><span class="k">风险说明</span><span class="v">{{ s.cmp.risk }}</span></div>
              <div v-if="s.cmp.related_law" class="wb-kv"><span class="k">相关法条</span><span class="v">{{ s.cmp.related_law }}</span></div>
              <div v-if="!referenceText" class="wb-muted">
                这份合同类型没有可用的标准条款范本，因此只显示「当前条款 ↔ 修改建议」两栏。
              </div>
            </div>
          </template>

          <!-- ② 未定位：提示 + 三种指定位置方式（**只有这一态才出现定位方式**） -->
          <template v-else-if="!isAddSession">
            <div class="wb-card">
              <div class="wb-card-t">
                定位状态
                <el-tag size="small" :type="locationState?.tone || 'warning'" effect="light">
                  {{ locationState?.label || '未定位' }}
                </el-tag>
              </div>
              <div class="wb-muted">
                系统没找到对应的原文。你可以告诉系统这段在合同哪里，或者直接描述它的内容。
              </div>
            </div>

            <div class="wb-card">
              <LocateClausePanel :ws="ws" />
            </div>

            <div class="wb-card">
              <div class="wb-card-t">合同里压根没有这条款？</div>
              <div class="wb-muted">如果合同里没有对应条款，进入新增条款流程。</div>
              <div class="wb-actions">
                <el-button size="small" type="success" @click="ws.openAddWorkspace(s)">新增条款</el-button>
              </div>
            </div>
          </template>

          <!-- ③ 新增条款工作区（右栏专属；中栏保持普通聊天工作区）
               状态 A：还没有条款正文 → 引导在中栏描述 + 选定位置后生成
               状态 B：已有正文 → 当前条款 + 说明/依据 + 插入位置 + 确认新增 -->
          <template v-else>
            <!-- 状态 A：还没有正文 -->
            <template v-if="!hasDraft">
              <div class="wb-card">
                <div class="wb-card-t">
                  新增条款
                  <el-tag size="small" type="warning" effect="light">待生成</el-tag>
                </div>
                <div class="wb-muted">这条款在合同里不存在，需要新增。</div>
              </div>

              <div class="wb-card">
                <div class="wb-card-t">还没有条款正文</div>
                <div class="wb-muted">
                  请先在<b>中栏</b>描述你希望新增的内容（例如"新增发票与税务条款，明确开票时间与税率"），
                  然后在下面选定插入位置，再生成推荐草案。
                </div>
                <div class="wb-actions wb-actions-top">
                  <el-button size="small" type="primary" plain @click="focusChatInput">去中栏描述</el-button>
                </div>
              </div>

              <div class="wb-card">
                <div class="wb-card-t">插入位置（必须由你选择）</div>
                <InlinePositionPicker
                  :ws="ws" :w="addW" :headings="addCtx.headings" :suggest="addCtx.suggest"
                  :can-use-suggest="addCtx.canUseSuggest" :before-prev="addBeforePrev"
                  :locate-candidates="addLocateCandidates"
                  :after-num="addAfterNum" :before-num="addBeforeNum"
                  @update:after-num="(v) => (addAfterNum = v)" @update:before-num="(v) => (addBeforeNum = v)"
                  @apply-after="addApplyAfter" @apply-before="addApplyBefore" @mode-change="addOnModeChange"
                  @confirm-position="addW.step = 4" @clear-position="addClearPosition"
                />
              </div>

              <div class="wb-card">
                <div class="wb-card-t">生成</div>
                <div class="wb-actions">
                  <el-button
                    type="primary"
                    :disabled="!canGenerate"
                    :loading="addW.submitting"
                    @click="ws.generateAddDraft(s)"
                  >生成推荐草案</el-button>
                </div>
                <div class="wb-muted">
                  生成后会立即保存为一条修改记录（不是未保存的草稿）；之后可在中栏继续多轮调整。
                </div>
              </div>
            </template>

            <!-- 状态 B：已有正文 -->
            <template v-else>
              <div class="wb-card">
                <div class="wb-card-t">
                  新增条款
                  <el-tag size="small" :type="ws.isSessionExportable(s.key) ? 'success' : 'warning'" effect="light">
                    {{ ws.isSessionExportable(s.key) ? '已确认修改' : '已保存为修改记录' }}
                  </el-tag>
                </div>
                <div class="wb-muted">这条款在合同里不存在，需要新增。</div>
              </div>

              <div class="wb-card">
                <div class="wb-card-t">当前新增条款</div>
                <ClauseDiffView
                  :before="''"
                  :after="s.lastRev?.revised_clause || ''"
                  before-label="（无原文）"
                  after-label="AI 起草的条款"
                />
                <div v-if="s.lastRev?.explanation" class="wb-exp">修改说明：{{ s.lastRev.explanation }}</div>
                <div v-if="s.lastRev?.legal_basis?.length" class="wb-chips">
                  <el-tag v-for="(lb, i) in s.lastRev.legal_basis" :key="i" size="small" effect="plain" type="warning">{{ lb }}</el-tag>
                </div>
                <div v-if="viewingRound != null" class="wb-muted wb-round-note">
                  正在查看第 {{ viewingRound + 1 }} 轮；右栏默认显示最新一轮。
                </div>
              </div>

              <div class="wb-card">
                <div class="wb-card-t">插入位置（必须由你选择）</div>
                <InlinePositionPicker
                  :ws="ws" :w="addW" :headings="addCtx.headings" :suggest="addCtx.suggest"
                  :can-use-suggest="addCtx.canUseSuggest" :before-prev="addBeforePrev"
                  :locate-candidates="addLocateCandidates"
                  :after-num="addAfterNum" :before-num="addBeforeNum"
                  @update:after-num="(v) => (addAfterNum = v)" @update:before-num="(v) => (addBeforeNum = v)"
                  @apply-after="addApplyAfter" @apply-before="addApplyBefore" @mode-change="addOnModeChange"
                  @confirm-position="addW.step = 4" @clear-position="addClearPosition"
                />
              </div>

              <div class="wb-card">
                <div class="wb-card-t">
                  操作
                  <el-tag v-if="shownRevIsAdopted" size="small" type="success" effect="light">✓ 已采用</el-tag>
                </div>
                <div class="wb-actions">
                  <el-button
                    type="primary"
                    :disabled="!currentPosition || !s.lastRev?.revised_clause || shownRevIsAdopted"
                    :loading="ws.adopting"
                    @click="ws.confirmAdopt(s, currentRev)"
                  >确认新增</el-button>
                  <el-button @click="focusChatInput">继续调整</el-button>
                </div>
                <div class="wb-muted">
                  确认新增后，这条新条款会按你选择的位置插入下载的合同文件。
                  继续调整生成的新版本不会自动取代它，需再次确认。
                </div>
              </div>
            </template>
          </template>

          <!-- 条款比对：状态为「符合标准」时不做修改按钮（V2.2 3.4） -->
          <div v-if="s.cmp && s.cmp.status === 'covered'" class="wb-card">
            <div class="wb-card-t">条款比对</div>
            <div class="wb-ok">✓ 条款符合标准</div>
            <div class="wb-muted">
              这条不需要修改。如果合同里还缺别的条款，可以用下方「新增条款」起草。
            </div>
            <div class="wb-actions">
              <el-button size="small" text @click="openAddForSession">新增其它条款</el-button>
            </div>
          </div>

          <!-- ④ 风险 / 条款信息（已定位与未定位都保留，紧凑展示） -->
          <div class="wb-card">
            <div class="wb-card-t">{{ s.cmp ? '条款比对' : '风险信息' }}</div>
            <template v-if="s.risk">
              <div class="wb-kv"><span class="k">风险</span><span class="v">{{ riskName(s.risk.risk_type) }}</span></div>
              <div class="wb-kv"><span class="k">等级</span><span class="v">{{ riskLevelLabel(s.risk.risk_level) }}</span></div>
              <div class="wb-kv"><span class="k">判定理由</span><span class="v">{{ s.risk.reason || '—' }}</span></div>
              <div class="wb-kv"><span class="k">修改建议</span><span class="v">{{ s.risk.suggestion || '—' }}</span></div>
              <div v-if="s.risk.risk_description" class="wb-kv"><span class="k">风险说明</span><span class="v">{{ s.risk.risk_description }}</span></div>
            </template>
            <template v-else-if="s.cmp">
              <div class="wb-kv"><span class="k">条款</span><span class="v">{{ s.cmp.title }}</span></div>
              <div class="wb-kv"><span class="k">状态</span><span class="v">{{ cmpStatusLabel }}</span></div>
              <div v-if="s.cmp.matched_text" class="wb-kv"><span class="k">当前条款</span><span class="v mono">{{ s.cmp.matched_text }}</span></div>
            </template>
            <div v-else class="wb-muted">
              这是历史修改记录，没有对应的风险或比对信息。
            </div>
          </div>

          <!-- ⑤ 审核员批注：后端无接口，只留位置（V2.2 8.3） -->
          <div class="wb-card wb-card-plan">
            <div class="wb-card-t">审核员批注<span class="wb-plan">规划中</span></div>
            <div class="wb-muted">当前版本分支暂未开放批注保存；可先在中栏直接写下你的意见。</div>
          </div>
        </template>

        <!-- 底部固定：下载状态条
             范围规则（V2.2 修正）：
               总体 → 显示全局合同状态 + 全局待处理清单（可点击进入专项）
               专项 → 只显示**当前会话**的修改状态，不出现全局清单/全局跳转
               历史 → 只读记录，不出现"再次修改"入口 -->
        <div class="wb-downloadbar">
          <div class="wb-dl-state" :class="'st-' + ws.docxState.key">
            <span class="wb-dl-mark">{{ dlMark }}</span>
            <span class="wb-dl-text">{{ downloadHeadline }}</span>
          </div>

          <!-- 总体：全局待处理清单 -->
          <template v-if="isOverview">
            <div class="wb-dl-detail">{{ ws.docxState.detail }}</div>
            <div v-if="unlocatedPoints.length" class="wb-dl-blockers">
              <div class="t">还有 {{ unlocatedPoints.length }} 个修改点待处理：</div>
              <div
                v-for="p in unlocatedPoints" :key="p.key"
                class="item" @click="ws.selectSession(p.key)"
              >{{ p.title }}<span class="wb-muted"> · 去处理</span></div>
            </div>
            <div v-else class="wb-dl-detail">当前没有待处理的修改点。</div>
          </template>

          <!-- 专项：只看当前会话 -->
          <template v-else-if="s">
            <div class="wb-dl-detail">{{ sessionDownloadDetail }}</div>
          </template>

          <div class="wb-actions">
            <el-button
              type="primary"
              :loading="ws.docx.loading"
              :disabled="ws.docxState.key === 'not_docx'"
              @click="ws.downloadDocx()"
            >
              {{ ws.docx.loading ? '正在生成…' : '下载修改后的合同' }}
            </el-button>
          </div>

          <el-alert
            v-if="ws.docx.error" type="error" show-icon :closable="false" class="wb-docx-err"
            title="下载失败"
          >
            <template #default>
              <div class="wb-docx-err-t">{{ ws.docx.error }}</div>
              <el-button size="small" @click="ws.downloadDocx()">重试</el-button>
            </template>
          </el-alert>

          <div v-if="ws.docxState.key === 'not_docx'" class="wb-dl-detail">
            当前上传的不是 Word 文档，暂不支持生成修改后的合同。请上传 .docx 格式的合同。
          </div>
          <div v-else class="wb-dl-note">
            下载的是<b>整份合同</b>：包含所有已确认的修改，不只是当前这一条。
            最终是否可生成以下载时的完整校验为准；若未通过，会原样显示具体原因。
          </div>
        </div>
      </aside>
    </div>

    <!-- 新增条款向导弹窗已移除：本文件的右栏内联工作区（InlinePositionPicker）
         是「新增条款」的唯一入口，它的 v-if="mode === 'dialog'" 因无人传入 mode 而永不渲染，
         属于死代码。新增条款的状态与位置规则仍全部由 useContractWorkspace 提供，
         没有第二套实现。 -->
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import LocateClausePanel from './LocateClausePanel.vue'
import InlinePositionPicker from './InlinePositionPicker.vue'
import ClauseDiffView from './ClauseDiffView.vue'
import OverviewPlanPanel from './OverviewPlanPanel.vue'
import { SESSION_GROUPS, displayInstruction, roundDiff } from '../../composables/useContractWorkspace.js'
import { riskName, riskLevelLabel } from '../../constants/riskTypes.js'

const props = defineProps({ ws: { type: Object, required: true } })

const s = computed(() => props.ws.activeSession)
const isOverview = computed(() => s.value?.key === '__overview__')
const isAddSession = computed(() => !!s.value && props.ws.sessionAction(s.value) === 'add_clause')
const uiFor = computed(() => (s.value ? props.ws.uiFor(s.value.key) : { input: '', pending: null }))

/**
 * 右栏"当前展示哪一轮"：默认最新一轮；点「查看此版对比」可切到历史轮次。
 *
 * 依赖里必须包含**会话身份**（key）：只依赖 rounds 时，从"1 轮的 A 会话"切到
 * "1 轮的 B 会话"不会触发 watch，viewingRound 会残留并指向 B 会话的错误轮次。
 * 同时用 lastRev.id 兜住"同一会话内轮数不变但内容被替换"的情况。
 */
const viewingRound = ref(null)
watch(
  () => [props.ws.activeKey, s.value?.key, s.value?.rounds, s.value?.lastRev?.id],
  () => { viewingRound.value = null },
)

const shownRev = computed(() => {
  const revs = s.value?.revs || []
  if (!revs.length) return null
  const i = viewingRound.value
  return i != null && revs[i] ? revs[i] : revs[revs.length - 1]
})
const confirmRev = computed(() => {
  const revs = s.value?.revs || []
  return revs.length ? revs[revs.length - 1] : null
})
/**
 * 右栏当前正在展示的那一轮（默认最新一轮，点了「查看此版对比」则是该历史轮）。
 * 「确认采用此版」采用的就是它——用户看到哪一版就确认哪一版。
 */
const currentRev = computed(() => shownRev.value || confirmRev.value)
/** 右栏当前这一轮是否已被采用（来自后端 adopted 字段，刷新后可恢复） */
const shownRevIsAdopted = computed(() => props.ws.isRevisionAdopted(currentRev.value))
/** 该会话当前被采用的轮次（同 clause_key 至多一条） */
const adoptedRev = computed(() => props.ws.sessionAdoptedRev(s.value))

// ── 标题 / 标签（用户语言，不出现 scope / operation / API 路径）──
const centerTitle = computed(() => {
  const v = s.value
  if (!v) return '未选择会话'
  if (isOverview.value) return '整份合同总控台'
  if (isAddSession.value) return `新增：${v.title}`
  if (v.compactTitle) return v.compactTitle
  return v.title
})
const groupTitle = (item) => {
  const t = String(item.title || '')
  // 风险标题形如「R08 · 验收标准缺失」，列表标题以业务名开头（编号不进标题前）
  const sep = t.indexOf(' · ')
  if (item.group === 'risk' && sep > 0) return t.slice(sep + 3)
  return t
}
const groupSubtitle = (item) => {
  if (item.key === '__overview__') return '汇总所有修改点，生成和管理整体修改方案'
  const base = String(item.subtitle || '').replace(/ · 已修改 \d+ 轮$/, '').replace(/ · 尚未修改$/, '')
  return base
}
const clauseNoLabel = computed(() => (s.value?.clauseNo ? clauseNoText(s.value.clauseNo) : ''))
function clauseNoText(no) {
  const n = props.ws.cnToInt(no)
  return n ? `第${props.ws.cnNo(n)}条` : ''
}

// ── 状态点（V2.2 2.2：绿=已确认修改 / 黄=已纳入方案 / 灰=未定位或未开始）──
const sessionState = computed(() => props.ws.sessionModificationState(s.value))
const stateTag = computed(() => (sessionState.value ? sessionState.value.tone : 'info'))
function itemDot(item) {
  if (item.key === '__overview__') return 'grey'
  const st = props.ws.sessionModificationState(item)
  return st ? st.dot : 'grey'
}
function itemStatusText(item) {
  if (item.key === '__overview__') return '整份合同总控台'
  const st = props.ws.sessionModificationState(item)
  if (st && st.key === 'confirmed') return '已确认修改'
  if (st && st.key === 'included') return st.label
  if (props.ws.sessionAction(item) === 'add_clause') return '待新增（需确认插入位置）'
  const badge = props.ws.locateBadge(item)
  if (badge.type === 'success' || badge.type === 'primary') return '已自动找到原文'
  return '未定位'
}

// ── 右栏对比数据 ──
/**
 * 右栏状态机（V2.2 修正）：
 *   isOverview       → 总体修改控制台（全局）
 *   isHistory        → 历史修改（**只读**）
 *   isAddSession     → 新增条款（正文 + 插入位置 + 确认新增）
 *   locationState    → 风险/比对：confirmed|locatable 进已定位工作区；unlocated 进未定位工作区
 *
 * `locationState` 统一了「左栏徽标 / 右栏工作区」的口径（见 workspaceLogic.LOCATION_STATES），
 * 并区分"系统能定位"与"用户已确认"。`relocateMode`（用户点「重新指定位置」）会把状态
 * 强制回到 unlocated —— 它只是前端工作态，不改动任何已落库修订。
 */
const locationState = computed(() => {
  if (!s.value || isOverview.value) return null
  return props.ws.locationStateForSession(s.value)
})
const locationStateKey = computed(() => locationState.value?.key || 'unlocated')
/** 已有可靠当前位置 → 进已定位工作区 */
const located = computed(() => !isOverview.value && !isAddSession.value && props.ws.hasReliableLocationNow(s.value))
/** 历史会话（不是当前审核/比对结果，仅为已保存的修改记录）→ 只读 */
const isHistory = computed(() => !isOverview.value && s.value?.group === 'history' && !isAddSession.value)

function hasLocation(v) {
  return props.ws.hasReliableLocationNow(v)
}
const locatedLabel = computed(() => {
  const v = s.value
  if (!v) return '—'
  const ui = props.ws.uiFor(v.key)
  const no = ui.confirmAnchor?.clause_no ?? v.risk?.clause_position?.clause_no ?? v.lastRev?.clause_no
  const title = ui.confirmAnchor?.clause_title || v.risk?.clause_position?.clause_title || ''
  const n = props.ws.cnToInt(no)
  return `${n ? clauseNoText(n) : '（无编号）'} ${title || ''}`.trim()
})

/** 定位来源说明：已落库锚点 / 用户本次确认 / 审核阶段找到 / 仅预检 */
const locatedSourceText = computed(() => {
  const v = s.value
  if (!v) return ''
  const ui = props.ws.uiFor(v.key)
  if (ui.confirmAnchor?.original_text) return '你确认的位置'
  if (v.lastRev?.original_clause_text) return '已保存修改使用的位置'
  if (v.risk?.clause_position?.original_text) return '审核阶段已自动找到原文'
  return '未确认（系统只是预检到了位置）'
})

const diffTitle = computed(() => {
  if (viewingRound.value == null) return '原文 ↔ 修改后（最新一轮）'
  return `原文 ↔ 修改后（第 ${viewingRound.value + 1} 轮）`
})
const diffBefore = computed(() => {
  const v = s.value
  if (!v) return ''
  if (viewingRound.value != null && v.revs[viewingRound.value]) {
    const rd = roundDiff(v.revs[viewingRound.value])
    return rd.before || s.value.clauseText || ''
  }
  const ui = props.ws.uiFor(v.key)
  return ui.confirmAnchor?.original_text || v.risk?.clause_position?.original_text || v.lastRev?.original_clause_text || v.clauseText || ''
})
const diffAfter = computed(() => shownRev.value?.revised_clause || '')
const diffBeforeLabel = computed(() => (viewingRound.value != null ? `第 ${viewingRound.value + 1} 轮的原文` : '合同原文'))
const diffAfterLabel = computed(() => (viewingRound.value != null ? `第 ${viewingRound.value + 1} 轮修改后` : '修改后'))

/** 比对的真实参考条款正文（拿不到 → 空串 → 只显示两栏） */
const referenceText = computed(() => (s.value?.cmp ? props.ws.referenceTextFor(s.value.cmp) : ''))
const cmpStatusLabel = computed(() => {
  const st = s.value?.cmp?.status
  return { covered: '条款符合标准', partial: '部分偏离', missing: '合同中缺少这条款', deviation: '存在偏离' }[st] || st || '—'
})

// ── 新增条款工作区（右栏专属）──
/** 当前会话是否已经有条款正文（= 已有一条 add_clause 修改记录） */
const hasDraft = computed(() => !!s.value?.lastRev?.revised_clause)

/** 插入位置：本次已确认的位置优先，其次该会话已保存记录上的位置 */
const currentPosition = computed(() => {
  const v = s.value
  if (!v) return null
  const ui = props.ws.uiFor(v.key)
  return ui.confirmAnchor?.position || v.lastRev?.position || null
})

/** 右栏位置选择器绑定的公共数据（推荐位置/条款列表，来自 useContractWorkspace） */
const addCtx = computed(() => props.ws.addPositionCtx || { suggest: null, canUseSuggest: false, headings: [] })
const addW = computed(() => props.ws.addWizard)
const addAfterNum = ref(null)
const addBeforeNum = ref(null)
const addBeforePrev = computed(() => (addBeforeNum.value == null ? null : props.ws.beforeAnchorOf(addBeforeNum.value)))
const addLocateCandidates = computed(() => {
  const r = addW.value?.locateResult
  if (!r) return []
  return r.candidates?.length ? r.candidates : r.found ? [r] : []
})

function addApplyAfter() {
  if (addAfterNum.value == null) return
  props.ws.chooseAddPosition('after', { anchor: addAfterNum.value })
}
function addApplyBefore() {
  if (addBeforeNum.value == null) return
  props.ws.chooseAddPosition('before', { num: addBeforeNum.value, prevAnchor: props.ws.beforeAnchorOf(addBeforeNum.value) })
}
function addOnModeChange(mode) {
  if (mode === 'suggest') props.ws.chooseAddPosition('suggest')
  else if (mode === 'append') props.ws.chooseAddPosition('append')
  else if (mode === 'after' && addAfterNum.value != null) addApplyAfter()
  else if (mode === 'before' && addBeforeNum.value != null) addApplyBefore()
  else {
    // 切换方式后清掉上一次的选定，避免沿用旧位置
    addW.value.confirmedPosition = null
    addW.value.posHint = ''
  }
}
function addClearPosition() {
  addW.value.confirmedPosition = null
  addW.value.posHint = ''
  addW.value.posMode = ''
  addAfterNum.value = null
  addBeforeNum.value = null
}

/** 是否具备生成条件：有需求描述 + 已由用户选定插入位置 */
const canGenerate = computed(() => !!s.value && props.ws.canGenerateAddDraft(s.value))

// 进入新增条款会话 → 只读拉一次参考数据（范本/法条/推荐位置），并带入中栏已输入的要求
watch(
  () => [props.ws.activeKey, isAddSession.value],
  async ([, isAdd]) => {
    if (!isAdd || !s.value) return
    addAfterNum.value = null
    addBeforeNum.value = null
    await props.ws.openAddWorkspace(s.value)
  },
  { immediate: true },
)

// ── 快捷意图 ──
const quickChips = computed(() => {
  if (isOverview.value) return ['整体倾向甲方', '整体倾向乙方', '生成修改清单']
  if (isAddSession.value) return ['用推荐模板', '简化条款', '补充违约责任']
  return ['按推荐条款修改', '更严格', '更宽松', '补强法律依据', '重新起草']
})
const placeholder = computed(() => {
  if (isOverview.value) return '对整个合同的修改要求…（例如：整体更保护甲方，但付款条件不要过苛）'
  if (isAddSession.value) return '继续修改新增条款，例如：把通知义务改为「15 日内书面通知」…'
  if (!s.value?.clauseText) return '请先在右侧确认修改位置，再描述你对这条款的修改要求'
  return '描述你对这条款的修改要求'
})

// ── 未定位修改点（**仅总体会话**展示的全局清单；专项会话不得显示）──
const unlocatedPoints = computed(() => {
  const list = props.ws.modificationPoints || []
  return list.filter((p) => p.state && (p.state.key === 'pending' || p.state.key === 'included') && !p.exportable)
})

// ── 下载状态条文案（按状态机分范围）──
/**
 * 状态条主文案。
 *
 * 范围规则：
 *   - 总体会话 → 显示**全局**合同状态（含"还有 N 条未建立定位"这类全局判定）
 *   - 专项/历史 → 只描述**当前这一条**，绝不出现全局条数
 *
 * 为什么专项不能直接用全局 `docxState.text`：它会出现"还有 N 条修改尚未建立可靠原文定位"
 * 这种全局判定，等于把一个合同的统计塞进单条会话的右栏。
 */
const downloadHeadline = computed(() => {
  if (!s.value || isOverview.value) return props.ws.docxState.text
  if (props.ws.docxState.key === 'not_docx') return '当前合同不是 Word 文档，不能生成修改后的合同'
  if (isHistory.value) return '历史修改记录（只读）'
  if (props.ws.docxState.key === 'none') return '当前合同还没有任何条款修改'
  if (props.ws.isSessionExportable(s.value.key)) return '这条修改已确认，可生成修改后的合同'
  if (isAddSession.value) return '这条新增条款还未确认插入位置'
  if (!located.value) return '这条修改还未确认位置'
  return '这条修改还没有可采纳的版本'
})

/** 专项会话的补充说明：只说当前会话，不报全局统计 */
const sessionDownloadDetail = computed(() => {
  const v = s.value
  if (!v) return ''
  if (isHistory.value) {
    return '这是已保存的历史修改记录。它已计入合同修改；需要改动请回到对应的风险或条款比对重新处理。'
  }
  if (props.ws.isSessionExportable(v.key)) {
    return '当前合同仍可下载修改版；最终能否生成以点击下载后的完整校验为准。'
  }
  if (isAddSession.value) {
    return '请先选择插入位置并点「确认新增」；系统不会替你决定插在哪里，也不会默认放到合同末尾。'
  }
  if (!located.value) {
    return '请先用下方任一种方式确认这条修改对应合同里的哪一段。'
  }
  return '确认无误后点「确认采用此版」，这条修改才会写入下载的合同文件。'
})

// ── 下载状态标记 ──
const dlMark = computed(
  () => ({ ready: '✓', blocked: '⚠', none: '⚠', not_docx: '⚠', loading: '…' }[props.ws.docxState.key] || '⚠'),
)

// ── 交互 ──
/**
 * 发送。
 *
 * 总体会话（V2.2 3.1）：用户在这里提的整体要求应当产出**结构化综合修改方案**，
 * 而不是把整份合同重写一遍存成一条"讨论稿"。因此这里改为调用
 * POST /overview/plan（只写方案表、不产生任何 ClauseRevision），
 * 由用户在右侧逐项查看、纳入、跳转专项处理。
 *
 * 各专项会话：保持既有语义（提交一轮修改 → ClauseRevision）。
 */
async function onSend() {
  const v = s.value
  if (!v) return
  const ui = props.ws.uiFor(v.key)
  const text = String(ui.input || '').trim()

  if (isOverview.value && text && !ui.refineMode) {
    ui.input = ''
    try {
      const data = await props.ws.generateProposal(text)
      if (data) ElMessage.success('已生成综合修改方案，请在右侧逐项处理')
    } catch {
      // 具体原因由请求层提示（原样展示后端 detail）
    }
    return
  }
  return props.ws.sendRevise()
}
function appendChip(c) {
  const ui = props.ws.uiFor(s.value.key)
  ui.input = ui.input ? ui.input + '；' + c : c
}
function viewRound(i) {
  viewingRound.value = i
}
function focusChatInput() {
  nextTick(() => {
    const el = document.querySelector('.wb-input textarea')
    if (el) el.focus()
  })
}
async function reloadAll() {
  await Promise.all([props.ws.loadRevisions(), props.ws.loadOverview(), props.ws.loadProposals()])
  ElMessage.success('已刷新')
}
/**
 * 「新增其它条款」：在同一合同里再起一条新增条款。
 *
 * 直接进右栏内联新增工作区（= 本组件里唯一的「新增条款」入口）：
 * 把本次预填的要求放进中栏输入框，再让 openAddWorkspace 把它带入 addWizard，
 * 用户在右栏 InlinePositionPicker 里必须显式选定插入位置后才能生成。
 *
 * 不再调用 openAddWizard：它只会把 addWizard.step 设为 2（「AI 建议」步骤），
 * 而右栏内联工作区固定只呈现第 3 步（插入位置），调用它反而会把状态设成不可用的步骤。
 */
function openAddForSession() {
  const item = s.value
  if (!item) return
  const prefill = item.risk
    ? (item.risk.risk_type === 'R09'
        ? '新增不可抗力条款，明确不可抗力的定义、通知义务与免责安排'
        : `新增缺失条款：${riskName(item.risk.risk_type)}`)
    : item.cmp
      ? `${item.cmp.title || '缺失条款'}：${(item.cmp.completion || '').trim() || '请依据标准范本起草该条款'}`
      : (item.lastRev ? displayInstruction(item.lastRev.instruction) : '')
  const ui = props.ws.uiFor(item.key)
  ui.input = prefill
  props.ws.openAddWorkspace(item)
}
async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t || '')
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择文本')
  }
}
function fmtTime(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ts
  }
}
function groupShort(g) {
  return { overview: '总体', risk: '风险', cmp: '比对', add: '新增', history: '历史' }[g] || g
}
function groupTag(g) {
  return { overview: 'info', risk: 'danger', cmp: 'warning', add: 'success', history: 'info' }[g] || 'info'
}
</script>

<style scoped>
.wb { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 10px; }
.wb-top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex-shrink: 0; }
.wb-top-l { display: flex; align-items: center; gap: 8px; min-width: 0; }
.wb-top-r { margin-left: auto; display: flex; gap: 8px; }
.wb-session-title { font-size: 15px; font-weight: 700; color: #131313; }
.wb-rounds { font-size: 12px; color: #8A93A6; }
.wb-spacer { flex: 1; }

.wb-body { display: flex; gap: 12px; flex: 1; min-height: 0; }

/* ── 左栏 240px ── */
.wb-left { flex: 0 0 240px; display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--a24-border); padding-right: 10px; }
.wb-stats { display: flex; gap: 6px; flex-shrink: 0; }
.wb-stat { flex: 1; background: #F8FAFD; border-radius: 8px; padding: 6px 4px; text-align: center; }
.wb-stat b { display: block; font-size: 17px; color: #131313; }
.wb-stat span { font-size: 10px; color: #8A93A6; }
.wb-stats-note { font-size: 10.5px; color: #9AA4B8; margin: 6px 0 8px; line-height: 1.55; flex-shrink: 0; }
.wb-left-err { font-size: 12px; color: #B91C1C; margin-bottom: 6px; }
.wb-groups { flex: 1; min-height: 0; overflow-y: auto; }
.wb-group-title { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: #6B7280; margin: 10px 2px 6px; }
.wb-group-title .n { background: #EEF1F6; color: #6B7280; border-radius: 8px; padding: 0 6px; font-size: 11px; }
.wb-group-empty { font-size: 11px; color: #C0C4CC; padding: 2px 2px 4px; line-height: 1.6; }
.wb-item { border: 1px solid var(--a24-border); border-left: 2px solid transparent; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; cursor: pointer; }
.wb-item:hover { background: #F8FAFD; }
.wb-item.is-active { border-color: #C7D2E5; border-left-color: var(--a24-primary); background: #F3F6FD; }
.wb-item-top { display: flex; align-items: center; gap: 6px; }
.wb-item-title { font-size: 12.5px; font-weight: 600; color: #131313; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wb-item-rounds { margin-left: auto; font-size: 11px; color: #16A34A; flex-shrink: 0; }
.wb-item-sub { font-size: 11.5px; color: #8A93A6; margin-top: 3px; }
.wb-item-locate { display: flex; align-items: center; gap: 5px; margin-top: 5px; }
.wb-item-locate .dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.wb-item-locate .txt { font-size: 11px; color: #8A93A6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot-grey { background: #C0C4CC; }
.dot-yellow { background: #D97706; }
.dot-blue { background: #1D4ED8; }
.dot-green { background: #16A34A; }

/* ── 中栏 ── */
.wb-center { flex: 1; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
.wb-ctx { flex-shrink: 0; background: #F8FAFD; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
.wb-ctx-row { display: flex; align-items: center; gap: 8px; }
.wb-ctx-row .k { font-size: 12px; color: #8A93A6; flex-shrink: 0; }
.wb-ctx-row .v { font-size: 13.5px; font-weight: 600; color: #131313; }
.wb-ctx-orig { display: flex; gap: 8px; margin-top: 6px; }
.wb-ctx-orig .k { font-size: 12px; color: #8A93A6; flex-shrink: 0; }
.wb-ctx-orig .v { font-size: 12.5px; color: #4B5563; line-height: 1.7; }
.wb-ctx-orig .v.muted { color: #C0C4CC; }
.wb-overview-note { flex-shrink: 0; margin-bottom: 8px; }
.wb-chat { flex: 1; min-height: 0; overflow-y: auto; padding-right: 4px; }
.wb-empty { font-size: 12.5px; color: #9AA4B8; text-align: center; padding: 36px 12px; line-height: 1.8; }
.wb-round { margin-bottom: 14px; }
.wb-round-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.wb-round-no { font-size: 12px; font-weight: 700; color: var(--a24-primary); }
.wb-muted { font-size: 11.5px; color: #9AA4B8; }
.wb-user-card { border: 1px solid var(--a24-border); background: #FBFCFE; border-radius: 8px; padding: 8px 10px; }
.wb-card-label { font-size: 11.5px; color: #8A93A6; margin-bottom: 4px; }
.wb-user-text { font-size: 12.5px; color: #303133; line-height: 1.75; white-space: pre-wrap; }
.wb-ai-card { border: 1px solid var(--a24-border); border-left: 3px solid var(--a24-primary); border-radius: 8px; padding: 10px 12px; margin-top: 6px; }
.wb-ai-text { font-family: "Noto Serif SC", "Songti SC", serif; font-size: 13px; line-height: 1.85; color: #1F2937; white-space: pre-wrap; word-break: break-word; margin: 0; }
.wb-ai-exp { font-size: 12px; color: #6B7280; margin-top: 6px; }
.wb-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.wb-ai-pos, .wb-ai-risk { font-size: 12px; margin-top: 6px; color: #B45309; }
.wb-ai-actions { margin-top: 6px; display: flex; gap: 4px; }

.wb-input { flex-shrink: 0; border-top: 1px solid var(--a24-border); padding-top: 8px; margin-top: 8px; }
.wb-refine-tip { font-size: 11.5px; color: #166534; background: #F0FDF4; border-radius: 6px; padding: 5px 8px; margin-bottom: 6px; }
.wb-refine-tip.warn { color: #B45309; background: #FFFBEB; }
.wb-chips-input { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 6px; }
.wb-chip { cursor: pointer; }
.wb-input-row { display: flex; gap: 8px; align-items: flex-end; }
.wb-input-row .el-textarea { flex: 1; }

/* ── 右栏 420px ── */
.wb-right { flex: 0 0 420px; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.wb-card { border: 1px solid var(--a24-border); border-radius: 10px; padding: 10px 12px; background: #fff; }
.wb-card-t {
  display: flex; align-items: center; gap: 6px;
  font-size: 12.5px; font-weight: 600; color: #6B7280; margin-bottom: 8px;
}
.wb-card-plan { background: #FCFCFD; }
.wb-plan { font-size: 10.5px; color: #9AA4B8; border: 1px dashed #D9DEE8; border-radius: 4px; padding: 0 4px; }
.wb-kv { display: flex; gap: 8px; font-size: 12px; line-height: 1.7; margin-bottom: 4px; }
.wb-kv .k { flex: 0 0 62px; color: #8A93A6; }
.wb-kv .v { flex: 1; color: #303133; word-break: break-word; white-space: pre-wrap; }
.wb-kv .v.mono { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; }
.wb-exp { font-size: 11.5px; color: #6B7280; line-height: 1.7; margin-top: 8px; }
.wb-list { margin: 0; padding-left: 16px; font-size: 12px; color: #B45309; line-height: 1.8; }
.wb-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.wb-ok { font-size: 12.5px; color: #166534; background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 6px; padding: 7px 9px; margin-bottom: 6px; }
.wb-warn { font-size: 12px; color: #B45309; background: #FFFBEB; border-radius: 6px; padding: 7px 9px; margin-bottom: 6px; line-height: 1.7; }
.wb-suggest { font-size: 12.5px; color: #B45309; display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }

/* 下载状态条：随右栏滚动内容自然排在最后一块（不再固定在右栏底部）。
   原来是 position: sticky + bottom: 0 + margin-top: auto，会把它钉在右栏可视底部，
   在「非 Word 文档」等状态下长期占住底部空间；现改为普通文档流块。
   卡片本身的尺寸/边框/留白/阴影保持原样，只增加与上方内容的间距。 */
.wb-downloadbar {
  margin-top: 10px;
  border: 1px solid var(--a24-border); border-radius: 10px; padding: 10px 12px;
  background: #fff; box-shadow: 0 -2px 10px rgba(19, 19, 19, 0.04);
  flex-shrink: 0;
}
.wb-dl-state { display: flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 600; }
.wb-dl-state.st-ready { color: #166534; }
.wb-dl-state.st-blocked, .wb-dl-state.st-none { color: #B45309; }
.wb-dl-state.st-not_docx { color: #6B7280; }
.wb-dl-mark { font-size: 13px; }
.wb-dl-text { flex: 1; }
.wb-dl-detail { font-size: 11.5px; color: #8A93A6; line-height: 1.7; margin: 4px 0 6px; }
.wb-dl-blockers { font-size: 11.5px; color: #B45309; border-top: 1px dashed var(--a24-border); padding-top: 6px; margin-bottom: 6px; }
.wb-dl-blockers .t { margin-bottom: 4px; }
.wb-dl-blockers .item { cursor: pointer; padding: 2px 0; color: #303133; }
.wb-dl-blockers .item:hover { color: var(--a24-primary); }
.wb-docx-err { margin-bottom: 6px; }
.wb-docx-err-t { font-size: 12px; line-height: 1.7; margin-bottom: 6px; }
.wb-dl-note { font-size: 11px; color: #9AA4B8; line-height: 1.6; margin-top: 4px; }

@media (max-width: 1440px) {
  .wb-right { flex: 0 0 380px; }
}
@media (max-width: 1200px) {
  .wb-body { flex-direction: column; }
  .wb-left, .wb-right { flex: 1 1 auto; border-right: none; }
}
</style>
