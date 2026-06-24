"""Cross-field consistency rules — pure functions over the brief.

Each rule inspects the partially-answered brief and may emit a warning if a
contradiction or red flag is detected. Rules MUST be cheap (no I/O, no LLM) so
detect_gaps() stays fast even on every-keypress refreshes.

Conventions:
- Rules receive a flat `dict[str, Any]` of {field_id: value} (skipped fields
  are absent, not None). This lets us write `if "x" in brief`.
- Rule returns `Optional[ConsistencyHit]`. None = no issue.
- `target_field_id` on the hit drives the FOLLOWUP rendering: when non-None,
  the followup prompt is "fix this field"; when None, it's a pure warning the
  user can `continue` past or `edit <field>` from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class ConsistencyHit:
    rule_id: str
    message: str
    target_field_id: Optional[str] = None  # None = pure warning


@dataclass(frozen=True)
class Rule:
    id: str
    check: Callable[[dict[str, Any]], Optional[ConsistencyHit]]


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

_WEEKS_RE = re.compile(r"(\d+)\s*(?:tuần|week|weeks?|w)", re.IGNORECASE)
_DAYS_RE = re.compile(r"(\d+)\s*(?:ngày|day|days?|d)", re.IGNORECASE)
_MONTHS_RE = re.compile(r"(\d+)\s*(?:tháng|month|months?|mo)", re.IGNORECASE)
_AVAIL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MONEY_RE = re.compile(r"(\d+(?:[\.,]\d+)?)", re.IGNORECASE)


def parse_deadline_weeks(text: Any) -> Optional[float]:
    """Best-effort: '6 tuần', '2 months', '14 days' → weeks. None if unparseable."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip().lower()
    m = _WEEKS_RE.search(s)
    if m:
        return float(m.group(1))
    m = _MONTHS_RE.search(s)
    if m:
        return float(m.group(1)) * 4.33
    m = _DAYS_RE.search(s)
    if m:
        return float(m.group(1)) / 7.0
    return None


def parse_availability_pct(text: Any) -> Optional[float]:
    """'99.99%' → 99.99. None if unparseable."""
    if not isinstance(text, str) or not text.strip():
        return None
    m = _AVAIL_RE.search(text)
    return float(m.group(1)) if m else None


_NUM_WITH_SUFFIX_RE = re.compile(
    r"(\d+(?:[\.,]\d+)?)\s*(triệu|million|tr|m|nghìn|nghin|thousand|k)?",
    re.IGNORECASE,
)


def parse_budget_usd(text: Any) -> Optional[float]:
    """'$200/mo', '5000 USD', '1k USD', '5tr VND' → USD float. None if unparseable.

    VND amounts get converted at a crude 25k VND/USD rate so the rule fires on
    obviously-tiny budgets without claiming precision.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip().lower()
    m = _NUM_WITH_SUFFIX_RE.search(s)
    if not m:
        return None
    try:
        raw = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if suffix in ("triệu", "million", "tr", "m"):
        mult = 1_000_000.0
    elif suffix in ("nghìn", "nghin", "thousand", "k"):
        mult = 1_000.0
    else:
        mult = 1.0
    value = raw * mult
    if "vnd" in s or "đồng" in s or "dong" in s or "₫" in s:
        return value / 25_000.0
    return value


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _has_any(value: Any, needles: list[str]) -> bool:
    """True if any needle appears (case-insensitive) in value or its list items."""
    items = _as_list(value)
    text_blob = " ".join(items).lower()
    return any(n.lower() in text_blob for n in needles)


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

def _avail_vs_budget(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    avail = parse_availability_pct(brief.get("availability_target"))
    budget = parse_budget_usd(brief.get("budget_infra"))
    if avail is None or budget is None:
        return None
    if avail >= 99.99 and budget < 200:
        return ConsistencyHit(
            rule_id="avail_vs_budget",
            message=(
                f"Availability {avail}% cần infra HA (multi-AZ, replicas, monitoring), "
                f"nhưng budget {budget:.0f} USD/mo khó đủ. Cần (a) hạ availability "
                "hoặc (b) tăng budget."
            ),
            target_field_id="budget_infra",
        )
    return None


def _scope_vs_deadline(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    scope = _as_list(brief.get("scope_in"))
    weeks = parse_deadline_weeks(brief.get("deadline"))
    if not scope or weeks is None or weeks <= 0:
        return None
    # ~1.5 items/week is aggressive; flag if higher
    if len(scope) / weeks > 1.5 and len(scope) >= 5:
        return ConsistencyHit(
            rule_id="scope_vs_deadline",
            message=(
                f"Scope có {len(scope)} mục, deadline ~{weeks:.1f} tuần "
                f"(~{len(scope) / weeks:.1f} mục/tuần). Cần cắt scope hoặc giãn deadline."
            ),
            target_field_id=None,
        )
    return None


def _residency_vs_deploy(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    residency = brief.get("data_residency")
    deploy = brief.get("deployment_target")
    if not isinstance(residency, str) or not isinstance(deploy, str):
        return None
    if "vn" not in residency.lower() and "việt" not in residency.lower():
        return None
    deploy_lower = deploy.lower()
    if any(p in deploy_lower for p in ("aws", "gcp", "azure")):
        # Allow if VN region is explicitly mentioned
        if "vn" in deploy_lower or "việt" in deploy_lower or "vietnam" in deploy_lower:
            return None
        return ConsistencyHit(
            rule_id="residency_vs_deploy",
            message=(
                f"Data phải ở VN ('{residency}'), nhưng deploy là '{deploy}' "
                "không nói rõ region VN. Confirm region hoặc đổi provider."
            ),
            target_field_id="deployment_target",
        )
    return None


def _team_vs_stack(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    stack = _as_list(brief.get("must_use_stack"))
    skills = _as_list(brief.get("team_skills"))
    if not stack or not skills:
        return None
    skill_blob = " ".join(skills).lower()
    missing = [s for s in stack if s.lower() not in skill_blob]
    if missing:
        return ConsistencyHit(
            rule_id="team_vs_stack",
            message=(
                f"Stack bắt buộc {missing} không xuất hiện trong team_skills "
                f"({skills}). Cần plan upskill / hire hoặc đổi stack."
            ),
            target_field_id="team_skills",
        )
    return None


def _greenfield_vs_existing_auth(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    kind = brief.get("greenfield_or_brownfield")
    auth = brief.get("existing_auth")
    if kind != "greenfield" or not isinstance(auth, str) or not auth.strip():
        return None
    if auth.strip().lower() in ("none", "không", "khong", "n/a", "-"):
        return None
    return ConsistencyHit(
        rule_id="greenfield_vs_existing_auth",
        message=(
            f"Project là greenfield nhưng existing_auth = '{auth}'. "
            "Confirm: là tích hợp SSO hay sẽ build auth mới hoàn toàn?"
        ),
        target_field_id=None,
    )


def _brownfield_vs_data_sources(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    kind = brief.get("greenfield_or_brownfield")
    sources = _as_list(brief.get("data_sources"))
    if kind != "brownfield":
        return None
    if not sources:
        return ConsistencyHit(
            rule_id="brownfield_vs_data_sources",
            message=(
                "Project là brownfield nhưng chưa liệt kê data_sources. "
                "Cần biết DB/API/file hiện có để plan migration."
            ),
            target_field_id="data_sources",
        )
    return None


def _user_count_year1_lt_now(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    now = _parse_user_count(brief.get("user_count_now"))
    yr1 = _parse_user_count(brief.get("user_count_year1"))
    if now is None or yr1 is None:
        return None
    if yr1 < now:
        return ConsistencyHit(
            rule_id="user_count_year1_lt_now",
            message=(
                f"User year1 ({yr1:.0f}) ít hơn user hiện tại ({now:.0f}). "
                "Confirm: project có dự kiến giảm user, hay nhập nhầm?"
            ),
            target_field_id="user_count_year1",
        )
    return None


def _parse_user_count(text: Any) -> Optional[float]:
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip().lower()
    mult = 1.0
    if re.search(r"\b(tr|triệu|million|m)\b", s):
        mult = 1_000_000.0
    elif re.search(r"\b(k|nghìn|nghin|thousand)\b", s):
        mult = 1_000.0
    m = _MONEY_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ".")) * mult
    except ValueError:
        return None


def _rps_vs_users(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    """Flag if expected_rps is implausibly high relative to user_count_now."""
    rps_text = brief.get("expected_rps")
    users = _parse_user_count(brief.get("user_count_now"))
    if not isinstance(rps_text, str) or users is None or users <= 0:
        return None
    m = _MONEY_RE.search(rps_text)
    if not m:
        return None
    try:
        rps = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    # >1 RPS per user is implausible for business apps
    if rps > users:
        return ConsistencyHit(
            rule_id="rps_vs_users",
            message=(
                f"Expected RPS ({rps:.0f}) cao hơn user_count_now ({users:.0f}). "
                "Confirm: có spike traffic gì đặc biệt, hay nhập nhầm đơn vị?"
            ),
            target_field_id="expected_rps",
        )
    return None


def _accessibility_vs_user_facing(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    """B2C-ish users + no accessibility set → likely gap, not strict error."""
    acc = brief.get("accessibility")
    primary = brief.get("primary_user")
    if not isinstance(primary, str):
        return None
    is_consumer_facing = any(
        kw in primary.lower()
        for kw in ("customer", "consumer", "public", "user", "khách", "công cộng")
    )
    if not is_consumer_facing:
        return None
    if isinstance(acc, str) and acc.strip() and acc.strip().lower() not in ("none", "không", "khong", "-"):
        return None
    return ConsistencyHit(
        rule_id="accessibility_vs_user_facing",
        message=(
            "Primary user có vẻ là người dùng cuối nhưng chưa khai báo accessibility. "
            "WCAG 2.1 AA là baseline khuyến nghị cho consumer-facing apps."
        ),
        target_field_id="accessibility",
    )


def _latency_vs_availability(brief: dict[str, Any]) -> Optional[ConsistencyHit]:
    """Strict latency (<100ms) + low availability target = mismatched expectations."""
    avail = parse_availability_pct(brief.get("availability_target"))
    lat = brief.get("latency_target")
    if avail is None or not isinstance(lat, str):
        return None
    m = re.search(r"(\d+)\s*ms", lat, re.IGNORECASE)
    if not m:
        return None
    ms = int(m.group(1))
    if ms < 100 and avail < 99.5:
        return ConsistencyHit(
            rule_id="latency_vs_availability",
            message=(
                f"Latency target {ms}ms rất chặt nhưng availability chỉ {avail}%. "
                "Cần align: latency thấp thường đi kèm SLA cao."
            ),
            target_field_id=None,
        )
    return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    Rule("avail_vs_budget", _avail_vs_budget),
    Rule("scope_vs_deadline", _scope_vs_deadline),
    Rule("residency_vs_deploy", _residency_vs_deploy),
    Rule("team_vs_stack", _team_vs_stack),
    Rule("greenfield_vs_existing_auth", _greenfield_vs_existing_auth),
    Rule("brownfield_vs_data_sources", _brownfield_vs_data_sources),
    Rule("user_count_year1_lt_now", _user_count_year1_lt_now),
    Rule("rps_vs_users", _rps_vs_users),
    Rule("accessibility_vs_user_facing", _accessibility_vs_user_facing),
    Rule("latency_vs_availability", _latency_vs_availability),
)


def check_all(brief: dict[str, Any]) -> list[ConsistencyHit]:
    """Run every rule, return all hits in registry order."""
    hits: list[ConsistencyHit] = []
    for rule in RULES:
        h = rule.check(brief)
        if h is not None:
            hits.append(h)
    return hits
