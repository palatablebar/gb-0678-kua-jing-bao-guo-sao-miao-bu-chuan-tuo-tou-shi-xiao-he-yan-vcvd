from app import node_policy
from app.errors import DomainError
from app.reasons import reject

DESTINATIONS={"JP":"Asia/Tokyo","US":"America/New_York","DE":"Europe/Berlin"}

def evaluate(rows,context):
    parameters=context.get("parameters",{}) if context else {}
    if parameters.get("node_policy","event_time") not in node_policy.POLICY_MODES:
        raise DomainError("BAD_NODE_POLICY","节点口径模式无效，可选 event_time/force_legacy/force_current")
    good=[];bad=[];primary=0;alternative_available=0
    for row in rows:
        zone=DESTINATIONS.get(row.get("destination"))
        if not zone:bad.append(reject(row,"UNKNOWN_DESTINATION","目的地未登记"));continue
        if not row.get("parcel"):bad.append(reject(row,"EMPTY_PARCEL","包裹号为空"));continue
        decision=node_policy.resolve(row.get("node"),row.get("event_time"),parameters)
        if not decision["known"]:
            bad.append(reject(row,"UNKNOWN_NODE","原始节点代码在新旧口径中均未登记"));continue
        anchor=decision["anchor"]
        canonicals={item["canonical"] for item in decision["interpretations"]}
        if len(canonicals)>1:alternative_available+=1
        else:primary+=1
        good.append({
            **row,
            "node":anchor["canonical"],
            "node_raw":decision["raw_node"],
            "zone":zone,
            "node_versions":[item["version"] for item in decision["interpretations"]],
            "node_anchor_version":decision["anchor_version"],
            "node_anchor_basis":decision["anchor_basis"],
            "node_interpretations":decision["interpretations"],
        })
    return {"rows":good,"quarantine":bad,"metrics":{
        "accepted":len(good),
        "destinations":sorted(set(r["destination"] for r in good)),
        "single_vocab_maps":primary,
        "reinterpreted_codes":alternative_available,
        "registry_fingerprint":node_policy.registry_fingerprint(),
    }}
