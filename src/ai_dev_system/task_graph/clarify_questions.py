"""Turn blocking spec findings into user-facing clarifying questions.

find_blocking: pure selection of error findings + needs_human facets.
synthesize_questions: collapse them into 1-3 Vietnamese questions via an injected
LLM, falling back to the raw messages on any failure (or when llm is None).
format_questions: render the Telegram message body.

No module here touches the network directly; the worker injects the LLM client so
this stays unit-testable and so the gateway can import format_questions cheaply.
"""
from __future__ import annotations

import json


def find_blocking(spec: dict) -> list[dict]:
    out: list[dict] = []
    for f in spec.get("findings") or []:
        if isinstance(f, dict) and f.get("severity") == "error":
            out.append({"kind": "finding", "key": f.get("section", "") or "",
                        "message": (f.get("message") or "").strip()})
    for key, facet in (spec.get("facets") or {}).items():
        if isinstance(facet, dict) and facet.get("status") == "needs_human":
            msg = (facet.get("content") or facet.get("reason") or "").strip()
            out.append({"kind": "facet", "key": key, "message": msg})
    return out


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            close = t.rfind("```")
            return t[nl + 1:close].strip() if close > nl else t[nl + 1:].strip()
    return t


_SYNTH_SYSTEM = (
    "Bạn là trợ lý kỹ thuật. Dưới đây là các điểm CHẶN mà hệ thống phát hiện khi "
    "đối chiếu yêu cầu với codebase thật. Hãy gộp chúng thành 1-3 CÂU HỎI ngắn gọn, "
    "rõ ràng bằng tiếng Việt để hỏi người yêu cầu — mỗi câu là một quyết định họ cần "
    "chốt. KHÔNG giải thích. Trả về DUY NHẤT một mảng JSON các chuỗi câu hỏi."
)


def synthesize_questions(blocking: list[dict], *, idea: str, llm) -> list[str]:
    if not blocking:
        return []
    if llm is not None:
        try:
            user = (
                "Yêu cầu của người dùng:\n" + (idea or "") + "\n\nCác điểm chặn:\n"
                + "\n".join(f"- [{b['kind']}/{b['key']}] {b['message']}" for b in blocking)
            )
            raw = llm.complete(system=_SYNTH_SYSTEM, user=user)
            parsed = json.loads(_strip_fence(raw))
            qs = [str(q).strip() for q in parsed if str(q).strip()]
            if qs:
                return qs[:3]
        except Exception:  # noqa: BLE001 — any failure → raw fallback below
            pass
    return [b["message"][:300] for b in blocking if b["message"]][:3]


def format_questions(questions: list[str]) -> str:
    lines = ["🤔 Mình cần bạn làm rõ vài điểm trước khi tiếp tục:"]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q}")
    lines.append("\nBạn trả lời thẳng trong tin nhắn tiếp theo nhé.")
    return "\n".join(lines)
