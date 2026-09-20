from .rule_engine import run_rules, RISK_RULES

# ── 纯加法再导出（省赛 Skill Registry 阶段）────────────────────────────
# 背景：审计发现本包原先只再导出 run_rules / RISK_RULES，另外 3 个**生产能力**
# 必须由调用方走全路径 import（如 api/contracts.py:30-32），包级接口与真实能力集不一致。
# 现补齐为完整包接口，供 ai/skills/adapters.py 统一引用。
#
# 安全性：**纯加法**——不修改任何既有实现、不改变上面一行的语义、
# 不改变任何函数的签名与返回结构；既有全路径 import 与 evaluate/ 脚本完全不受影响。
from .evidence_extractor import extract_evidence_detailed  # noqa: F401
from .evidence_adjudicator import adjudicate_risks  # noqa: F401
from .recommendation_engine import build_recommendations  # noqa: F401
