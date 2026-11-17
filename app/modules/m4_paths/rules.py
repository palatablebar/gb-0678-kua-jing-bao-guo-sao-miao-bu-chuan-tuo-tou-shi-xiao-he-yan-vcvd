from collections import defaultdict
from app.values import instant
from app.reasons import reject,sources
from app import node_policy
from app.node_policy import PRIORITY


def _order(events):
    return sorted(events,key=lambda r:(instant(r["event_utc"]),PRIORITY[r["node"]],r["scan_id"]))


def _validate(events):
    """对一种口径解释下的事件序列做路径校验，返回(有效事件, 事件级错误)。"""
    ordered=_order(events);seen={};last=-1;valid=[];errors=[]
    for e in ordered:
        time=e["event_utc"]
        if time in seen and seen[time]!=e["node"]:
            errors.append((e,"SIMULTANEOUS_CONFLICT","同刻扫描节点冲突"));continue
        seen[time]=e["node"]
        if PRIORITY[e["node"]]<last:
            errors.append((e,"PATH_REGRESSION","扫描路径倒退"));continue
        last=PRIORITY[e["node"]];valid.append(e)
    if not any(e["node"]=="ACCEPT" for e in valid):
        errors.append((ordered[0],"MISSING_ACCEPT","缺少收件扫描"))
    return valid,errors


def _plans(events,parameters):
    """生成候选口径方案。

    首选方案是各扫描按事件时刻命中的口径（anchor）；存在改义代码时，
    再以“逐扫描切换到另一口径解释”生成备选方案。强制口径模式只产出该口径方案。
    """
    forced=(parameters or {}).get("node_policy") or "event_time"
    base=[]
    for e in events:
        interp=e["node_interpretations"]
        chosen=next((i for i in interp if i["version"]==e["node_anchor_version"]),interp[0])
        base.append({"event":e,"interp":chosen})
    plans=[{"name":"anchor","picks":base}]
    if forced=="event_time":
        for idx,entry in enumerate(base):
            for interp in entry["event"]["node_interpretations"]:
                if interp["version"]==entry["interp"]["version"]:continue
                picks=[dict(p) for p in base];picks[idx]={"event":entry["event"],"interp":interp}
                plans.append({"name":"alt:line{}".format(entry["event"].get("_line",idx)),"picks":picks})
    # 同形序列去重：不同方案可能解释出完全相同的标准节点序列
    unique={};deduped=[]
    for plan in plans:
        key=tuple(p["interp"]["canonical"] for p in plan["picks"])
        if key in unique:continue
        unique[key]=1;deduped.append(plan)
    return deduped,forced


def _materialize(plan):
    material=[]
    for p in plan["picks"]:
        event=p["event"];interp=p["interp"]
        overridden=interp["version"]!=event["node_anchor_version"]
        material.append({**event,"node":interp["canonical"],
                         "node_applied_version":interp["version"],
                         "node_applied_basis":interp["basis"],
                         "node_overridden":overridden})
    return material


def _compat(rows):
    """兼容直接进入路径阶段的手工行：补齐口径解释字段，全部按单一锚点口径处理。"""
    for r in rows:
        if "node_interpretations" not in r:
            r["node_interpretations"]=[{"version":node_policy.CURRENT_VERSION,
                                        "canonical":r["node"],"basis":"direct_preview"}]
            r["node_anchor_version"]=node_policy.CURRENT_VERSION
            r.setdefault("node_raw",r["node"])
    return rows


def evaluate(rows,context):
    parameters=context.get("parameters",{}) if context else {}
    rows=_compat(rows)
    groups=defaultdict(list);good=[];bad=[]
    for r in rows:groups[r["parcel"]].append(r)
    fallback_count=0;forced_failures=0
    for parcel,events in sorted(groups.items()):
        plans,forced=_plans(events,parameters)
        evaluated=[]
        for plan in plans:
            material=_materialize(plan)
            valid,errors=_validate(material)
            evaluated.append((plan,material,valid,errors))
        chosen=next((item for item in evaluated if not item[3]),None)
        is_fallback=chosen is not None and chosen[0]["name"]!="anchor"
        if chosen is None:
            # 没有任何口径能形成完全有效的路径：沿用首选口径，保留有效事件，
            # 问题事件按原规则逐条隔离，并登记全部尝试过的口径与失败原因。
            plan,material,valid,errors=evaluated[0]
            attempts=[{"plan":p["name"],
                       "path":" → ".join(m["node"] for m in m_events),
                       "errors":[code for _,code,_ in errs]}
                      for p,m_events,_v,errs in evaluated]
            for event,code,detail in errors:
                bad.append(reject({**event,"node_policy_attempts":attempts,
                                   "node_policy_forced":forced},code,detail))
            if forced!="event_time":forced_failures+=1
            if not valid:continue
            policy_result="primary"
            policy_detail="首选口径部分事件无效，问题事件已隔离"
        else:
            plan,material,valid,errors=chosen
            used_versions=sorted({e["node_applied_version"] for e in valid})
            if plan["name"]=="anchor":
                policy_result="primary";policy_detail="事件时刻口径直接成立"
            else:
                policy_result="fallback";fallback_count+=1
                policy_detail="首选口径路径无效，回退到备选口径 {}".format("、".join(used_versions))
        ordered_valid=_order(valid)
        accepts=[e for e in ordered_valid if e["node"]=="ACCEPT"]
        if not accepts:continue
        delivery=next((e for e in ordered_valid if e["node"]=="DELIVERED"),None)
        first=accepts[0]
        source_lines=[line for e in ordered_valid for line in sources(e)]
        interpretations=[{"scan_id":e["scan_id"],"source_lines":sources(e),"raw_node":e["node_raw"],
                          "anchor_version":e["node_anchor_version"],
                          "applied_version":e["node_applied_version"],
                          "overridden":e.get("node_overridden",False),
                          "canonical":e["node"],"basis":e["node_applied_basis"],
                          "alternatives":[{"version":i["version"],"canonical":i["canonical"]}
                                          for i in e["node_interpretations"]
                                          if i["version"]!=e["node_applied_version"]]}
                         for e in ordered_valid]
        good.append({**first,"_sources":source_lines,
                     "accepted_at":first["event_utc"],
                     "delivered_at":delivery["event_utc"] if delivery else None,
                     "delivery_uploaded_at":delivery["received_utc"] if delivery else None,
                     "scan_count":len(ordered_valid),
                     "path":" → ".join(e["node"] for e in ordered_valid),
                     "reported":delivery["reported"] if delivery else first["reported"],
                     "node_policy_result":policy_result,
                     "node_policy_detail":policy_detail,
                     "node_versions_applied":sorted({e["node_applied_version"] for e in ordered_valid}),
                     "node_cutover_at":node_policy.cutover_of(parameters).isoformat(),
                     "interpretations":interpretations})
    return {"rows":good,"quarantine":bad,"metrics":{
        "parcels":len(good),
        "delivered":sum(r["delivered_at"] is not None for r in good),
        "path_errors":len(bad),
        "policy_primary":sum(r["node_policy_result"]=="primary" for r in good),
        "policy_fallback":fallback_count,
        "forced_policy_failures":forced_failures,
    }}
