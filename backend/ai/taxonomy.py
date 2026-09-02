"""
ai.taxonomy — 合同分类体系（唯一真源）

主体框架（法理全集）：
    主体
    ├─ 民事合同（民法典合同编）
    │   ├─ 典型合同（19 类，合同编第二分编 第9~27章）
    │   └─ 无名合同（服务外包 / 保密 / 其他，民法典第467条）
    └─ 劳动合同（特别法《劳动合同法》）

设计原则：
  1. 「框架」与「落地」分离——框架保留完整法理全集，`enabled` 标记当前实际开发
     的类别（10 类：7 典型 + 2 无名 + 1 特别法），其余 `enabled=False` 随时可开；
  2. 分类器、条款比对、前端下拉全部从这里读取，不各写一份硬编码；
  3. 加新类别 = 在本模块登记 + 补该类标准条款/法条数据，代码逻辑零改动。
"""
from typing import List, Dict, Any

# ── 法理归属大类 ──
KIND_TYPICAL = "典型合同"
KIND_UNNAMED = "无名合同"
KIND_SPECIAL = "特别法"


# ── 典型合同：民法典合同编第二分编 第9~27章，共 19 类 ──
# enabled=True 表示当前落地开发（已选 7 类）
TYPICAL_CONTRACTS: List[Dict[str, Any]] = [
    {"name": "买卖合同", "chapter": "第9章", "enabled": True},
    {"name": "供用电、水、气、热力合同", "chapter": "第10章", "enabled": False},
    {"name": "赠与合同", "chapter": "第11章", "enabled": False},
    {"name": "借款合同", "chapter": "第12章", "enabled": False},
    {"name": "保证合同", "chapter": "第13章", "enabled": False},
    {"name": "租赁合同", "chapter": "第14章", "enabled": True},
    {"name": "融资租赁合同", "chapter": "第15章", "enabled": False},
    {"name": "保理合同", "chapter": "第16章", "enabled": False},
    {"name": "承揽合同", "chapter": "第17章", "enabled": True},
    {"name": "建设工程合同", "chapter": "第18章", "enabled": True},
    {"name": "运输合同", "chapter": "第19章", "enabled": False},
    {"name": "技术合同", "chapter": "第20章", "enabled": True},
    {"name": "保管合同", "chapter": "第21章", "enabled": False},
    {"name": "仓储合同", "chapter": "第22章", "enabled": False},
    {"name": "委托合同", "chapter": "第23章", "enabled": True},
    {"name": "物业服务合同", "chapter": "第24章", "enabled": False},
    {"name": "行纪合同", "chapter": "第25章", "enabled": False},
    {"name": "中介合同", "chapter": "第26章", "enabled": True},
    {"name": "合伙合同", "chapter": "第27章", "enabled": False},
]

# ── 无名合同：民法典第467条（适用合同编通则 + 参照最相类似有名合同）──
# refer_to = 风险条款判断时参照的最相类似有名合同
UNNAMED_CONTRACTS: List[Dict[str, Any]] = [
    {"name": "服务外包合同", "enabled": True, "refer_to": ["技术合同", "委托合同"]},
    {"name": "保密协议", "enabled": True, "refer_to": []},
    {"name": "无名合同", "enabled": True, "refer_to": []},  # 兜底（培训/养老/电商/医疗美容等）
]

# ── 特别法合同（独立于民事合同）──
SPECIAL_CONTRACTS: List[Dict[str, Any]] = [
    {"name": "劳动合同", "law": "劳动合同法", "enabled": True},
]


def enabled_names() -> List[str]:
    """当前已启用（落地开发）的类别名，按 典型→无名→特别 顺序。"""
    names: List[str] = []
    for c in TYPICAL_CONTRACTS:
        if c.get("enabled"):
            names.append(c["name"])
    for c in UNNAMED_CONTRACTS:
        if c.get("enabled"):
            names.append(c["name"])
    for c in SPECIAL_CONTRACTS:
        if c.get("enabled"):
            names.append(c["name"])
    return names


# 分类器使用的类别清单（自动生成，不再手写）
ENABLED_TYPES: List[str] = enabled_names()


def kind_of(name: str) -> str:
    """返回某类别名的法理归属大类（典型/无名/特别法）。"""
    for c in TYPICAL_CONTRACTS:
        if c["name"] == name:
            return KIND_TYPICAL
    for c in UNNAMED_CONTRACTS:
        if c["name"] == name:
            return KIND_UNNAMED
    for c in SPECIAL_CONTRACTS:
        if c["name"] == name:
            return KIND_SPECIAL
    return ""


def refer_to(name: str) -> List[str]:
    """无名合同的参照有名合同（民法典467条）；非无名合同返回空列表。"""
    for c in UNNAMED_CONTRACTS:
        if c["name"] == name:
            return c.get("refer_to", [])
    return []


# ── 别名映射：分类器类别 → 标准条款库内部 type ──
# 标准条款库(standard_clauses.json)已重打为 11 类口径：买卖/服务外包/保密协议/
# 技术合同/劳动合同 各有独立 type；承揽/委托/建设/中介/租赁 尚未单列，
# 暂用最相近的 type 做条款比对（后续补齐后移除本映射）。
TYPE_ALIAS: Dict[str, str] = {
    "承揽合同": "服务外包合同",      # 定制开发 → 服务类条款
    "委托合同": "服务外包合同",      # 委托 → 服务类条款
    "建设工程合同": "服务外包合同",  # 暂用服务类，待补建设类条款
    "中介合同": "服务外包合同",      # 居间 → 服务类条款
    "租赁合同": "买卖合同",          # 暂用买卖条款，待补租赁类条款
}


def to_dict() -> Dict[str, Any]:
    """返回完整框架（JSON 可序列化），供前端 /api/contract-types 使用。"""
    return {
        "framework": {
            "民事合同": {
                "典型合同": TYPICAL_CONTRACTS,
                "无名合同": UNNAMED_CONTRACTS,
            },
            "劳动合同": SPECIAL_CONTRACTS,
        },
        "enabled": ENABLED_TYPES,
    }
