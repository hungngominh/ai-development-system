# src/ai_dev_system/task_graph/spec_messages.py
"""User-facing chat strings for the spec gate — shared by the gateway's
dev_pipeline tools (pull: user asks for progress) and SpecStatusWatcher
(push: daemon announces terminal states). One source of truth so both
surfaces always say the same thing."""
from __future__ import annotations


def spec_error_message(spec: dict) -> str:
    err = str(spec.get("error") or "")[:300]
    return f"❌ Tạo spec thất bại: {err}\nNhắn lại nội dung task để thử lại."


def spec_gate_message(spec: dict) -> str:
    url = spec.get("spec_doc_url")
    link = f"\n📄 Spec: {url}" if url else ""
    if spec.get("doc_publish_failed"):
        link = ("\n⚠️ Không push được spec doc lên repo (kiểm tra "
                "git credentials trong container) — file chỉ có ở bản clone local.")
    return f"📄 Spec sẵn sàng.{link}\nNhắn 'duyệt' để tạo plan."
