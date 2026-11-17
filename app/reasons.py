REASONS={"BAD_COLUMNS":"列数不匹配","EMPTY_VALUE":"必填值为空","INVALID_NUMBER":"数字格式错误","INVALID_TIME":"时间缺少偏移或格式错误","DUPLICATE_CONFLICT":"同键取值冲突","UNKNOWN_REFERENCE":"引用不存在","STALE_VERSION":"版本已更新","EMPTY_FILE":"文件为空","HEADER_ONLY":"仅有表头","BLANK_ONLY":"全部为空行","UNKNOWN_NODE":"原始节点代码在任何口径版本中均未登记","NODE_POLICY_NO_VALID_PATH":"新旧口径均无法形成有效路径，隔离待人工裁决","NODE_POLICY_CONFLICT":"强制口径与事件时刻口径冲突且该口径下路径无效"}
def reject(row, code, detail):
    return {**row,"reason":code,"detail":detail}
def sources(row):
    return row.get("_sources", [row.get("_line",0)])
