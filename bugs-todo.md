# Bugs & TODO

## Open

### [BUG] 显式指定 model 时不校验 capabilities，不支持的参数直接透传到 API

**影响模型**：`doubao-seedream-3-0-t2i`（只有 `text_to_image`，无图生图），以及未来任何能力受限的 Seedream 变体。

**复现路径**：
```python
generate_image(model="doubao-seedream-3-0-t2i", reference_images=["https://..."])
# → SeedreamAdapter.build_payload 把 image 字段打进 payload
# → CFGPU API 返回错误，而非 adapter 层的友好提示
```

**根因**：`router.resolve()` 在显式指定 model 时走 `get_adapter()` 直接返回，跳过了 `supports()` 检查；`supports()` 目前只在 auto 路由的 `select_model()` 里被调用。

**备选方案**：
- A：接受现状，API 报错兜底（不改代码）
- B（推荐）：`router.resolve()` 补 `supports()` 调用 + `SeedreamAdapter` override `supports()` 检查 capabilities
- C：`build_payload` 里按 capabilities 静默过滤（不推荐，用户体验差）

**相关文件**：`router.py:36-41`, `adapters/seedream.py`, `adapters/base.py:99`
