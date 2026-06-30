"""Verify the in-app LLM key setting: data preserved, set/status/test/clear flow works."""
import json, os, urllib.request as u, urllib.error

B = os.environ.get("AUREA_BASE", "http://localhost:8010")


def call(path, data=None, token=None, method=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request(B + path, data=body, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with u.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    admin = call("/api/auth/login-json", {"email": "admin@aurea.demo", "password": "aurea"})[1]["access_token"]

    # Data preserved (no reseed)
    hh = call("/api/core/households", token=admin)[1]
    print(f"DATA PRESERVED: {len(hh)} households")
    assert len(hh) >= 10

    # Initial status
    st = call("/api/admin/llm", token=admin)[1]
    print(f"INITIAL: enabled={st['enabled']} anthropic_configured={st['anthropic_configured']}")

    # Set a (dummy) key — proves the plumbing; a real key would pass the test call.
    s2 = call("/api/admin/llm", {"anthropic_api_key": "sk-ant-dummy-test-key-not-real"}, token=admin, method="PUT")[1]
    print(f"AFTER SET: enabled={s2['enabled']} anthropic_configured={s2['anthropic_configured']}")
    assert s2["enabled"] and s2["anthropic_configured"]

    # Confirm the key is NOT leaked back anywhere
    firm = call("/api/admin/firm", token=admin)[1]
    leaked = "dummy" in json.dumps(firm)
    print(f"KEY LEAKED IN /firm? {leaked}")
    assert not leaked

    # Test connection (dummy key → graceful failure)
    t = call("/api/admin/llm/test", {}, token=admin)[1]
    print(f"TEST (dummy key): ok={t['ok']} msg={t.get('message')}")
    assert t["ok"] is False  # invalid key fails cleanly

    # Non-admin (adviser) cannot set the key
    adv = call("/api/auth/login-json", {"email": "sophie.adviser@aurea.demo", "password": "aurea"})[1]["access_token"]
    code, _ = call("/api/admin/llm", {"anthropic_api_key": "x"}, token=adv, method="PUT")
    print(f"ADVISER set key -> {code} (expect 403)")
    assert code == 403

    # Clear it (leave clean state for the demo)
    s3 = call("/api/admin/llm", {"anthropic_api_key": ""}, token=admin, method="PUT")[1]
    print(f"AFTER CLEAR: enabled={s3['enabled']} anthropic_configured={s3['anthropic_configured']}")
    assert not s3["anthropic_configured"]

    print("\nLLM SETTING VERIFY PASSED")


if __name__ == "__main__":
    main()
