"""Regenerate the bilingual catalogue and audit CSV coverage (no source edits)."""
import csv
import json
from collections import defaultdict
from pathlib import Path
import sys

from cfgpu_mcp.error_normalization import MCP_RULES, RULES, SUBMISSION_LOST_REMEDIES, _reasons, match_error


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
        "generate_image / generate_video / generate_audio / task_status / task_wait 的 MCP 错误响应保留原有 content.message；命中时仅通过 structuredContent.message_i18n 返回 {zh, en} 两种归一化文案。非终态结果（task_status / task_wait，或 generate_* 未等到终态）携带的 last_error 同样原样保留在 content；命中时 structuredContent.last_error 为该 last_error 的完整副本外加 message_i18n，宿主将 structuredContent 合并进 content 后原字段不丢失。",
        "不改 tasks.error、原始响应、error_type、retryable 或关联 ID；非 MCP 调用（Mode B / CLI）保持原有文案。未命中时不添加 message_i18n。",
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
    lines += ["", f"未匹配行：{missing}。这些记录仅有 failed 状态而无错误原因（部分外层为 200/success）；不能据此推断具体原因，沿用原有失败处理。", f"全空行：{empty}，不构造错误。"]
    lines += ['', '## MCP 侧文案', '', '以下为本服务自己产生的错误文案（如 request_id 不存在、缺少 request_id、提交结果丢失、超时与网络失败），先按上表匹配上游原因，未命中再按下表匹配。整句翻译（含本服务给出的处理建议，因为 content.message 中已有）；原文为中文的，中文与 content.message 原文一致；原文为英文的（如「Task … not found.」）给出中文译文；{raw}/{detail} 为上游原文或异常文本，原样填入。', 'submission_lost 的文案为「原因 + 补救说明」：两段都能翻译时拼接返回；原因未命中则整体不添加 message_i18n，避免客户端展示的信息少于模型看到的。', '', '| 规则 ID | 匹配模式（正则） | 中文 | English |', '| --- | --- | --- | --- |']
    for rule in (*MCP_RULES, *SUBMISSION_LOST_REMEDIES):
        values = (rule.key, rule.pattern, rule.zh, rule.en)
        lines.append("| " + " | ".join(v.replace("|", "&#124;") for v in values) + " |")
    lines += ["", "再生成：`python scripts/document_error_normalization.py src/model_generation_tasks_202611131529.csv`。", ""]
    Path("error-normalization.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"matched={sum(map(len, hits.values()))}, empty={len(empty)}, unmatched={missing}")


if __name__ == "__main__":
    main()
