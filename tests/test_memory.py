async def test_store_retrieve_update_delete(rt):
    m = rt.memory.remember("my project folder is on D drive", "project", key="project folder")
    rt.memory.remember("I prefer dark mode and Chrome", "preference")
    rt.memory.remember("the wifi router is in the hallway", "long_term")
    hits = rt.memory.search("where is my project folder")
    assert hits and hits[0]["id"] == m["id"]
    assert rt.memory.search("quantum chromodynamics lattice") == []
    upd = rt.memory.remember("my project folder is on E drive", "project", key="project folder")  # same key → update
    assert upd["id"] == m["id"] and "E drive" in rt.memory.get(m["id"])["content"]
    assert len(rt.memory.list("project")) == 1
    assert rt.memory.forget(m["id"])
    assert rt.memory.list("project") == []


async def test_secrets_are_never_stored(rt):
    r = await rt.registry.execute("remember", {"content": "my api key is sk-ant-abcdefghijklmnop1234"})
    assert not r.success and "secret" in r.error.lower() or "key" in r.error.lower()
    r = await rt.registry.execute("remember", {"content": "my password is hunter2"})
    assert not r.success
    assert rt.memory.list() == []


async def test_memory_tools_and_disable(rt):
    r = await rt.registry.execute("remember", {"content": "Assistant name is Jarvis", "category": "preference"})
    assert r.success and r.verified
    r = await rt.registry.execute("recall_memory", {"query": "assistant name"})
    assert r.result["matches"][0]["content"] == "Assistant name is Jarvis"
    rt.settings.update({"memory": {"enabled": False}})
    r = await rt.registry.execute("remember", {"content": "x"})
    assert r.status == "unavailable"


async def test_multilingual_recall(rt):
    rt.memory.remember("मेरा प्रोजेक्ट फोल्डर D ड्राइव में है", "project")
    assert rt.memory.search("प्रोजेक्ट फोल्डर")
