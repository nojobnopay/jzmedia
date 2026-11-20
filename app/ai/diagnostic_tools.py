"""Choose from the read-only checks offered for this observation. No write tools or URLs."""

from .client import AiUnavailable, call_json


_INSTRUCTION = """为小映选择下一步只读诊断。根据症状类别和已经获得的证据，从 tools 选择有助于区分原因的检查，
按优先级返回 {"tools":["工具名"]}。最多选择三个，只使用给出的名称。服务器负责地址、参数、网络权限和结果验证。"""

# Convert free text into a small diagnostic vocabulary before contacting a model.
_SYMPTOMS = {
    "decode": ("解码", "格式", "decode"), "network": ("代理", "网络", "超时", "proxy", "network"),
    "media": ("分集", "这集", "媒体", "线路", "分片", "media", "episode"),
    "catalogue": ("目录", "接口", "catalog", "api"), "cache": ("缓存", "cache"),
}


def choose_tools(question: str, evidence: list[dict], options: dict[str, str], defaults: list[str]) -> list[str]:
    """Ask at most once per decision, degrade to the caller's local policy on ambiguity."""
    fallback = [name for name in defaults if name in options][:3]
    if len(options) < 2:
        return fallback
    recent = [{key: row[key] for key in ("tool", "outcome") if key in row}
              for row in evidence[-8:]]
    payload = {"symptoms": [name for name, words in _SYMPTOMS.items() if any(word in question for word in words)],
               "observations": recent, "tools": options}
    try:
        result = call_json("diagnostic_tools", payload, _INSTRUCTION, timeout_limit=4)
    except AiUnavailable:
        return fallback
    if not isinstance(result, dict):
        return fallback
    names = result.get("tools")
    if (set(result) != {"tools"} or not isinstance(names, list) or not 1 <= len(names) <= 3
            or any(not isinstance(name, str) or name not in options for name in names)):
        return fallback
    return list(dict.fromkeys(names))
