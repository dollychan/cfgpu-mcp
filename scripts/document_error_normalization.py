"""Regenerate the bilingual catalogue and audit CSV coverage (no source edits)."""
import csv
import json
from collections import defaultdict
from pathlib import Path
import sys

from cfgpu_mcp.error_normalization import RULES, _reasons, match_error


def main():
    source = Path(sys.argv[1])
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig", newline="")))
    hits = defaultdict(list)
    missing = []
    empty = []
    for number, row in enumerate(rows, 2):
        if not any(row.values()):
            empty.append(number)
            continue
        candidates = []
        for field in ("query_response_json", "response_json"):
            if row[field]:
                try:
                    body = json.loads(row[field])
                except ValueError:
                    candidates.append(row[field])
                else:
                    if isinstance(body, dict):
                        candidates.extend(_reasons(body))
        candidates.append(row["error_message"])
        for candidate in candidates:
            found = match_error(candidate)
            if found:
                hits[found[0].key].append(number)
                break
        else:
            missing.append(number)
    lines = [
        "# Generate 上游错误归一化表",
        "",
        f"来源：`{source.as_posix()}`，共 {len(rows)} 条记录。",
        f"命中明确原因 {sum(map(len, hits.values()))} 条；空记录 {len(empty)} 条；未匹配 {len(missing)} 条。",
        "",
        "generate_image / generate_video / generate_audio / task_status / task_wait 的 MCP 错误响应保留原有 content.message；命中时仅通过 structuredContent.message_i18n 返回 {zh, en} 两种归一化文案。",
        "不改 tasks.error、原始响应、error_type、retryable 或关联 ID；非终态轮询 last_error 和非 MCP 调用保持原有文案。未命中时不添加 message_i18n。",
        "DeerFlow 的 langchain-mcp-adapters 将协议 structuredContent 映射为 ToolMessage.artifact['structured_content']。MessageStreamMiddleware._emit_tool_result 将其合并到客户端事件 content，随后移除临时 side channel；模型读取的 ToolMessage.content 保持原样。",
        "当前 MessageStreamMiddleware 只合并字段，不选择语言。消费方应按 locale 完整标签、主语言（如 zh-CN → zh）、原始 message 的顺序回退，仅更改展示文案，不改模型消息。",
        "优先使用结构化错误原因，其次匹配现有错误文案。表中规则按顺序匹配，动态数值按上游填入，不将某个模型的限制推广到其他模型。",
        "不按 InvalidParameter、task_failed 等通用错误码兜底归一化。未命中时保留原有回传文案及行为。",
        "内容审核仅描述上游判断，不断言素材实际违规；不推断退款、扣费或重试安全性。",
        "默认仅描述失败原因和上游明确的参数约束，不推导处理建议；仅当规则匹配的所有上游变体均明确包含建议时，才可保留该建议。",
        "",
        "| 规则 ID | 上游匹配模式（正则） | 中文 normalized | English normalized | 样本数 | CSV 行号 |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for rule in RULES:
        values = (rule.key, rule.pattern, rule.zh, rule.en, str(len(hits[rule.key])), ", ".join(map(str, hits[rule.key])))
        lines.append("| " + " | ".join(v.replace("|", "&#124;") for v in values) + " |")
    lines += ["", f"未匹配行：{missing}。这些记录仅有 failed 状态而无错误原因（部分外层为 200/success）；不能据此推断具体原因，沿用原有失败处理。", f"全空行：{empty}，不构造错误。", "", "再生成：`python scripts/document_error_normalization.py src/model_generation_tasks_202611131529.csv`。", ""]
    Path("error-normalization.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"matched={sum(map(len, hits.values()))}, empty={len(empty)}, unmatched={missing}")


if __name__ == "__main__":
    main()
