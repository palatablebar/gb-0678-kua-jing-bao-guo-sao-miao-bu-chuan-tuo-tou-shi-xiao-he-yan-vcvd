"""节点口径版本化（m2 节点映射）。"""
from app.modules.m2_nodes.rules import evaluate
from app import node_policy


def _row(node, event, parcel="P1"):
    return {"scan_id": node + event[:10], "parcel": parcel, "node": node,
            "event_time": event, "destination": "JP"}


def test_legacy_window_uses_old_vocabulary():
    # 旧窗口（切换 2026-11-10 之前）的 CN 按旧义解释为清关
    out = evaluate([_row("CN", "2026-11-06T00:00:00Z")], {"parameters": {}})
    r = out["rows"][0]
    assert r["node"] == "CUSTOMS" and r["node_anchor_version"] == node_policy.LEGACY_VERSION
    assert r["node_anchor_basis"] == "event_in_legacy_window"


def test_current_window_uses_new_vocabulary():
    out = evaluate([_row("CN", "2026-11-12T00:00:00Z")], {"parameters": {}})
    r = out["rows"][0]
    assert r["node"] == "DEPART" and r["node_anchor_version"] == node_policy.CURRENT_VERSION


def test_late_upload_keeps_event_time_anchor():
    # 本月才补传的旧扫描，锚点仍是事件时刻，不会被新口径改写
    out = evaluate([_row("CN", "2026-11-06T00:00:00Z")], {"parameters": {}})
    assert out["rows"][0]["node"] == "CUSTOMS"


def test_both_interpretations_kept_as_evidence():
    out = evaluate([_row("CN", "2026-11-12T00:00:00Z")], {"parameters": {}})
    versions = {i["version"]: i["canonical"] for i in out["rows"][0]["node_interpretations"]}
    assert versions == {node_policy.LEGACY_VERSION: "CUSTOMS",
                        node_policy.CURRENT_VERSION: "DEPART"}


def test_common_alias_single_vocab_but_reinterpreted_code_flagged():
    out = evaluate([_row("收件", "2026-11-12T00:00:00Z"),
                    _row("CN", "2026-11-12T00:00:00Z", "P2")], {"parameters": {}})
    m = out["metrics"]
    assert m["single_vocab_maps"] == 1 and m["reinterpreted_codes"] == 1
    assert len(m["registry_fingerprint"]) == 16


def test_force_legacy_overrides_event_window():
    out = evaluate([_row("CN", "2026-11-12T00:00:00Z")],
                   {"parameters": {"node_policy": "force_legacy"}})
    assert out["rows"][0]["node"] == "CUSTOMS"
    assert out["rows"][0]["node_anchor_basis"] == "manual_force_legacy"


def test_force_current_overrides_event_window():
    out = evaluate([_row("CN", "2026-11-06T00:00:00Z")],
                   {"parameters": {"node_policy": "force_current"}})
    assert out["rows"][0]["node"] == "DEPART"


def test_unknown_code_unknown_in_every_vocabulary():
    out = evaluate([_row("ZZ", "2026-11-12T00:00:00Z")], {"parameters": {}})
    assert out["quarantine"][0]["reason"] == "UNKNOWN_NODE" and not out["rows"]


def test_bad_policy_mode_rejected():
    from app.errors import DomainError
    import pytest
    with pytest.raises(DomainError) as exc:
        evaluate([_row("CN", "2026-11-12T00:00:00Z")], {"parameters": {"node_policy": "future"}})
    assert exc.value.code == "BAD_NODE_POLICY"


def test_cutover_parameter_respected():
    out = evaluate([_row("CN", "2026-11-08T00:00:00Z")],
                   {"parameters": {"node_cutover": "2026-11-01T00:00:00Z"}})
    assert out["rows"][0]["node_anchor_version"] == node_policy.CURRENT_VERSION


def test_unparseable_event_time_defaults_to_current_vocabulary():
    version, basis = node_policy.version_for_event("not-a-time")
    assert version == node_policy.CURRENT_VERSION
    assert basis == "event_time_unparseable_fallback_current"


def test_registry_fingerprint_is_stable():
    assert node_policy.registry_fingerprint() == node_policy.registry_fingerprint()
    assert len(node_policy.registry_fingerprint()) == 16
