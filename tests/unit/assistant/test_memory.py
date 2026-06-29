from ai_dev_system.assistant.memory import MemoryStore, Memory


def test_load_empty_when_no_files(tmp_path):
    store = MemoryStore(tmp_path)
    mem = store.load()
    assert isinstance(mem, Memory)
    assert mem.agent == ""
    assert mem.user == ""


def test_add_then_load_roundtrip(tmp_path):
    store = MemoryStore(tmp_path)
    store.write("MEMORY", "add", "Prefers Vietnamese.")
    store.write("USER", "add", "Role: solo dev.")
    mem = store.load()
    assert "Prefers Vietnamese." in mem.agent
    assert "Role: solo dev." in mem.user


def test_replace_overwrites(tmp_path):
    store = MemoryStore(tmp_path)
    store.write("MEMORY", "add", "old")
    store.write("MEMORY", "replace", "new only")
    assert store.load().agent.strip() == "new only"


def test_remove_drops_matching_line(tmp_path):
    store = MemoryStore(tmp_path)
    store.write("MEMORY", "add", "keep")
    store.write("MEMORY", "add", "drop me")
    store.write("MEMORY", "remove", "drop me")
    mem = store.load()
    assert "keep" in mem.agent
    assert "drop me" not in mem.agent


def test_invalid_target_raises(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        store.write("OTHER", "add", "x")
        raised = False
    except ValueError:
        raised = True
    assert raised
