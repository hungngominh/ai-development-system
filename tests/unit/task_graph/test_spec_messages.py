from ai_dev_system.task_graph.spec_messages import spec_error_message, spec_gate_message


def test_error_message_truncates_and_prompts_retry():
    msg = spec_error_message({"status": "error", "error": "boom " * 200})
    assert msg.startswith("❌ Tạo spec thất bại: ")
    assert "Nhắn lại nội dung task để thử lại." in msg
    assert len(msg) < 400


def test_gate_message_with_doc_url():
    msg = spec_gate_message({"status": "done", "spec_doc_url": "https://x/blob/b/s.md"})
    assert "📄 Spec sẵn sàng." in msg and "https://x/blob/b/s.md" in msg
    assert "Nhắn 'duyệt' để tạo plan." in msg


def test_gate_message_publish_failed_warns():
    msg = spec_gate_message({"status": "done", "doc_publish_failed": True})
    assert "⚠️" in msg and "git credentials" in msg


def test_gate_message_plain():
    msg = spec_gate_message({"status": "done"})
    assert msg == "📄 Spec sẵn sàng.\nNhắn 'duyệt' để tạo plan."
