"""Role-Based Access Control (RBAC) — the single source of truth for admin roles.

Roles: owner (env-immutable), deputy_owner, general_supervisor,
referrals_supervisor, support_supervisor, observer.

Security rules baked into this module (and re-checked in every handler):
  * ``owner`` can NEVER be granted through the bot — only env OWNER_ID.
  * env ADMIN_IDS are fixed as ``general_supervisor`` (config-secure).
  * Runtime grants live in the ``extra_admins`` table and are only writable
    from the owner panel (owner-gated in code, see handlers/owner_panel.py).
"""
from __future__ import annotations

from config.settings import ADMIN_IDS, OWNER_ID
from middlewares.auth import is_owner

# Permission keys checked by handlers/admin.py:
#   admin.view        — open panel + view-only screens (users, health, engine, audit)
#   admin.users       — ban / unban users
#   admin.support     — support tickets (view + reply + close)
#   admin.maintenance — toggle maintenance mode
#   admin.engine      — update yt-dlp
#   admin.network     — referral network + clone instances screens

ROLE_DEFS = {
    "owner": {
        "rank": 100, "emoji": "👑", "label": "المالك الأساسي",
        "desc": "تحكم كامل — ثابت من البيئة فقط",
        "perms": {"*"},
    },
    "deputy_owner": {
        "rank": 80, "emoji": "🛡️", "label": "نائب المالك",
        "desc": "كل صلاحيات التشغيل، بدون إدارة الرتب أو إعدادات المالك المحمية",
        "perms": {"admin.view", "admin.users", "admin.support", "admin.maintenance",
                  "admin.engine", "admin.network"},
    },
    "general_supervisor": {
        "rank": 60, "emoji": "🎖️", "label": "مشرف عام",
        "desc": "إدارة المستخدمين والحظر + الدعم + الصيانة والمحرك",
        "perms": {"admin.view", "admin.users", "admin.support", "admin.maintenance",
                  "admin.engine"},
    },
    "referrals_supervisor": {
        "rank": 50, "emoji": "🤝", "label": "مشرف إحالات ونسخ",
        "desc": "مراجعة الإحالات والنسخ فقط — بدون حظر أو إعدادات",
        "perms": {"admin.view", "admin.network"},
    },
    "support_supervisor": {
        "rank": 40, "emoji": "🛟", "label": "مشرف دعم فني",
        "desc": "متابعة التذاكر والرد عليها — بدون صلاحيات إدارية",
        "perms": {"admin.view", "admin.support"},
    },
    "observer": {
        "rank": 20, "emoji": "📊", "label": "مراقب وإحصائيات",
        "desc": "قراءة الأرقام والتقارير فقط — لا يمكنه تغيير أي شيء",
        "perms": {"admin.view"},
    },
}

# Roles the owner may actually grant through the panel (owner is env-only).
GRANTABLE_ROLES = [
    "deputy_owner", "general_supervisor", "referrals_supervisor",
    "support_supervisor", "observer",
]


def role_info(role: str) -> dict:
    return ROLE_DEFS.get(role, ROLE_DEFS["observer"])


def role_for_user(user_id) -> str | None:
    """Resolve the effective role. Precedence: owner > env admins > runtime grant."""
    if not user_id:
        return None
    uid = int(user_id)
    if is_owner(uid):
        return "owner"
    if uid in set(ADMIN_IDS):
        return "general_supervisor"
    try:
        from database.bot_settings import get_extra_admin
        staff = get_extra_admin(uid)
    except Exception:
        staff = None
    if staff and staff.get("role") in ROLE_DEFS:
        return staff["role"]
    return None


def has_perm(user_id, perm: str) -> bool:
    role = role_for_user(user_id)
    if not role:
        return False
    perms = ROLE_DEFS[role].get("perms", set())
    return "*" in perms or perm in perms


def can_manage_role(actor_role: str, target_role: str) -> bool:
    """Whether ``actor_role`` may assign/revoke ``target_role``.

    Owner may assign any grantable role. Others must be strictly higher-ranked
    and can never touch ``owner`` or their own rank.
    """
    if not actor_role or not target_role:
        return False
    if actor_role == "owner":
        return target_role in GRANTABLE_ROLES
    if target_role == "owner" or target_role == actor_role:
        return False
    return ROLE_DEFS.get(actor_role, {}).get("rank", 0) > \
           ROLE_DEFS.get(target_role, {}).get("rank", 0)
