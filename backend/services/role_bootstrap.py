"""services.role_bootstrap — 部署/开发初始化：只负责建立「第一个管理员」，仅此一次。

背景
----
公开注册已固定为 uploader（见 api/auth.py），reviewer/approver/admin 不开放自助获取。
为避免"系统里再无任何合法途径产生管理员"，提供一个**一次性的部署初始化**开关：

    环境变量 BOOTSTRAP_ADMIN_USERNAME = <一个已经存在的账号用户名>

行为规则（安全约束，改动前请先读完）
------------------------------------
1. 未配置该变量 → 完全跳过（默认行为，绝不影响任何账号）。
2. 库中**已经存在 admin** → 完全跳过，**不修改任何角色**。
   这条是刻意的：避免管理员在后台把自己降权后，重启服务器又被强行恢复成 admin。
3. 配置的用户名**不存在** → 跳过并告警，**绝不自动创建账号**
   （否则会出现密码未知的管理员）。
4. 仅当"库中 0 个 admin"且"指定账号已存在"时，把该账号提升为 admin。
5. 不修改任何账号的密码，不删除账号，不做任何批量角色修改。

即：本模块最多把**一个已存在的账号**从非 admin 提升为 admin，且只在整个系统还没有
任何管理员时发生一次。之后所有角色分配都由管理员通过 /api/auth/users/{id}/role 完成。
"""
import logging
import os

from sqlalchemy.orm import Session

from api.deps import ROLE_ADMIN
from models.user import User

logger = logging.getLogger(__name__)

ENV_VAR = "BOOTSTRAP_ADMIN_USERNAME"

# 返回值（同时用于日志与测试断言）
SKIPPED_NOT_CONFIGURED = "skipped:not_configured"
SKIPPED_ADMIN_EXISTS = "skipped:admin_exists"
SKIPPED_USER_NOT_FOUND = "skipped:user_not_found"
PROMOTED = "promoted"


def bootstrap_admin(db: Session) -> str:
    """按上述规则尝试建立第一个管理员；返回本次行为描述。任何情况下都不抛异常给调用方。"""
    username = (os.getenv(ENV_VAR) or "").strip()
    if not username:
        return SKIPPED_NOT_CONFIGURED

    admin_count = db.query(User).filter(User.role == ROLE_ADMIN).count()
    if admin_count > 0:
        logger.info("bootstrap：库中已有 %d 个管理员，跳过（不修改任何角色）", admin_count)
        return SKIPPED_ADMIN_EXISTS

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        logger.warning(
            "bootstrap：%s=%s 指定的账号不存在，跳过（不会自动创建账号）", ENV_VAR, username
        )
        return SKIPPED_USER_NOT_FOUND

    if user.role == ROLE_ADMIN:  # 理论上已被上面的 admin_count 拦截，兜底
        return SKIPPED_ADMIN_EXISTS

    user.role = ROLE_ADMIN
    db.commit()
    logger.warning("bootstrap：库中原本没有管理员，已将现有账号 %s 提升为 admin", username)
    return PROMOTED
