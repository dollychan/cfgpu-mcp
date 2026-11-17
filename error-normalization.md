# Generate 上游错误归一化表

来源：`src/model_generation_tasks_202611131529.csv`，共 230 条记录。
命中明确原因 213 条；空记录 14 条；未匹配 3 条。

generate_image / generate_video / generate_audio / task_status / task_wait 的 MCP 错误响应保留原有 content.message；命中时仅通过 structuredContent.message_i18n 返回 {zh, en} 两种归一化文案。非终态结果（task_status / task_wait，或 generate_* 未等到终态）携带的 last_error 同样原样保留在 content；命中时 structuredContent.last_error 为该 last_error 的完整副本外加 message_i18n，宿主将 structuredContent 合并进 content 后原字段不丢失。
不改 tasks.error、原始响应、error_type、retryable 或关联 ID；非 MCP 调用（Mode B / CLI）保持原有文案。未命中时不添加 message_i18n。
DeerFlow 的 langchain-mcp-adapters 将协议 structuredContent 映射为 ToolMessage.artifact['structured_content']。MessageStreamMiddleware._emit_tool_result 将其合并到客户端事件 content，随后移除临时 side channel；模型读取的 ToolMessage.content 保持原样。
当前 MessageStreamMiddleware 只合并字段，不选择语言。消费方应按 locale 完整标签、主语言（如 zh-CN → zh）、原始 message 的顺序回退，仅更改展示文案，不改模型消息。
优先使用结构化错误原因，其次匹配现有错误文案。表中规则按顺序匹配，动态数值按上游填入，不将某个模型的限制推广到其他模型。
不按 InvalidParameter、task_failed 等通用错误码兜底归一化。未命中时保留原有回传文案及行为。
内容审核仅描述上游判断，不断言素材实际违规；不推断退款、扣费或重试安全性。
默认仅描述失败原因和上游明确的参数约束，不推导处理建议；仅当规则匹配的所有上游变体均明确包含建议时，才可保留该建议。

| 规则 ID | 上游匹配模式（正则） | 中文 normalized | English normalized | 样本数 | CSV 行号 |
| --- | --- | --- | --- | ---: | --- |
| real_person_image | input image(?: '[^']+')* may contain real person | 上游因参考图片可能包含真人而拒绝请求。 | The provider rejected a reference image that may contain a real person. | 27 | 7, 33, 44, 46, 47, 48, 49, 50, 51, 55, 56, 57, 59, 63, 64, 65, 66, 67, 72, 73, 74, 75, 76, 99, 116, 129, 131 |
| real_person_video | input video(?: '[^']+')* may contain real person | 上游因参考视频可能包含真人而拒绝请求。 | The provider rejected a reference video that may contain a real person. | 4 | 42, 52, 53, 54 |
| input_text_copyright | input text may be related to copyright restrictions | 输入文本可能涉及版权限制，被上游拒绝。 | The provider rejected input text due to possible copyright restrictions. | 1 | 30 |
| output_image_copyright | output image may be related to copyright restrictions | 生成图片可能涉及版权限制，被上游拦截。 | The generated image was blocked due to possible copyright restrictions. | 2 | 36, 69 |
| output_video_copyright | output video may be related to copyright restrictions | 生成视频可能涉及版权限制，被上游拦截。 | The generated video was blocked due to possible copyright restrictions. | 6 | 152, 153, 160, 173, 198, 200 |
| output_audio_copyright | output audio may be related to copyright restrictions | 生成音频可能涉及版权限制，被上游拦截。 | The generated audio was blocked due to possible copyright restrictions. | 1 | 195 |
| input_ip | Input data is suspected of being involved in IP infringement | 输入素材疑似涉及知识产权侵权，被上游拒绝。 | The provider rejected input suspected of intellectual property infringement. | 1 | 171 |
| input_text_safety | input text(?: '[^']+')* may contain sensitive information&#124;Your prompt was blocked by the content safety policy&#124;提示词不符合平台内容规范 | 提示词未通过上游内容审核。 | The prompt failed the provider's content safety review. | 24 | 81, 82, 83, 84, 85, 87, 89, 90, 91, 92, 93, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 130, 205 |
| input_safety | Your prompt or reference image was blocked by the content safety policy | 提示词或参考图片未通过内容审核。 | The prompt or a reference image failed content safety review. | 1 | 110 |
| output_image_safety | The generated image was filtered by the safety policy | 生成图片被上游安全策略拦截。 | The generated image was blocked by the provider's safety policy. | 3 | 168, 169, 179 |
| output_video_safety | output video may contain sensitive information | 生成视频未通过上游内容审核。 | The generated video failed the provider's content safety review. | 1 | 132 |
| output_audio_safety | output audio may contain sensitive information | 生成音频未通过上游内容审核。 | The generated audio failed the provider's content safety review. | 2 | 166, 167 |
| safety | Your request was rejected by the safety system&#124;内容审核未通过: 违规 | 请求未通过上游内容审核。 | The request failed the provider's content safety review. | 11 | 163, 164, 165, 176, 177, 182, 183, 186, 187, 188, 189 |
| image_min_pixels | image size must be at least (?P<limit>\d+) pixels | 输出图片总像素不足，至少需要 {limit} 像素。 | The output image is too small; at least {limit} pixels are required. | 2 | 8, 9 |
| image_max_pixels | image size must be at most (?P<limit>\d+) pixels | 输出图片总像素超限，最多允许 {limit} 像素。 | The output image is too large; at most {limit} pixels are allowed. | 2 | 19, 20 |
| reference_image_pixels | expected the pixel to be at least (?P<limit>\d+)px, but received a (?P<size>\d+x\d+)px image | 参考图片尺寸为 {size}，总像素不足 {limit}。 | The reference image is {size}, below the minimum of {limit} pixels. | 2 | 43, 45 |
| media_min_pixels | the parameter (?P<media>video&#124;image) pixel count specified in the request must be greater than or equal to (?P<limit>\d+) | 参考素材（{media}）总像素不足，至少需要 {limit} 像素。 | The reference {media} has too few pixels; at least {limit} pixels are required. | 4 | 41, 61, 62, 139 |
| media_max_duration | the parameter (?P<media>video total&#124;video&#124;audio) duration \(seconds\) specified in the request must be less than or equal to (?P<limit>[\d.]+) | 参考素材时长超限：{media} 时长不得超过 {limit} 秒。 | Reference duration exceeds the limit: {media} duration must not exceed {limit} seconds. | 11 | 26, 27, 28, 29, 35, 38, 39, 40, 68, 77, 78 |
| media_min_duration | the parameter (?P<media>audio&#124;video) duration \(seconds\) specified in the request must be greater than or equal to (?P<limit>[\d.]+) | 参考素材（{media}）过短，至少需要 {limit} 秒。 | The reference {media} is too short; at least {limit} seconds are required. | 2 | 58, 115 |
| video_max_duration | Video duration can not longer than (?P<limit>[\d.]+)s | 视频时长不得超过 {limit} 秒。 | Video duration must not exceed {limit} seconds. | 2 | 184, 185 |
| audio_only | reference_audio cannot be the only reference input | 不能仅使用参考音频。 | Reference audio cannot be the only reference input. | 1 | 34 |
| return_last_frame | The parameter `return_last_frame` specified in the request is not valid\. | 上游判定 return_last_frame 参数无效。 | The provider reported that the return_last_frame parameter is invalid. | 8 | 11, 12, 13, 14, 15, 16, 17, 18 |
| png_required | transparent background requires a PNG input | 透明背景处理需要有效的 PNG 输入。 | Transparent background processing requires valid PNG input. | 1 | 37 |
| layer_decomposition | the image content could not be processed for layer decomposition | 上游无法对该图片进行图层分解。 | The provider could not decompose this image into layers. | 3 | 60, 79, 138 |
| invalid_image | Invalid image file or mode for image (?P<index>\d+) | 第 {index} 张图片的文件或颜色模式不受支持。 | Image {index} has an invalid file format or color mode. | 2 | 180, 181 |
| invalid_video_url | Invalid video_url\. | 视频 URL 无效。 | The video URL is invalid. | 1 | 193 |
| download_timeout | Timeout while downloading url:&#124;failed to download \d+ image\(s\):.*context deadline exceeded | 上游下载参考素材超时。 | The provider timed out downloading reference media. | 2 | 10, 190 |
| media_unreachable | media url unreachable\. | 上游无法访问素材 URL。 | The provider cannot access a media URL. | 1 | 191 |
| reference_images | 最多支持 16 张参考图，且必须为 HTTP\(S\) 图片 URL | 参考图片最多 16 张，且必须使用 HTTP(S) 图片 URL。 | At most 16 reference images are supported, using HTTP(S) image URLs. | 1 | 94 |
| prompt_required | Field validation for 'Prompt' failed on the 'required' tag | 提示词缺失。 | The prompt is missing. | 1 | 70 |
| seconds_type | cannot unmarshal number into Go struct field VideoCreateRequest.seconds of type string | 上游要求 seconds 为字符串，但收到数字。 | The provider requires seconds as a string but received a number. | 1 | 71 |
| aspect_ratio_required | Aspect ratio must be specified when no first image and not video editing | 未提供首帧且非视频编辑时，必须指定宽高比。 | An aspect ratio is required without a first frame when not editing video. | 1 | 170 |
| media_required | Field required: input\.media | 上游缺少必填 input.media。 | The provider is missing required input.media. | 1 | 172 |
| edit_constraints | Seedance identified your task as video editing.*4 to 30 seconds.*`ratio` must be `adaptive`.*`duration` must be -1 | 上游将任务识别为视频编辑，但参数不符合要求：输入视频须为 4–30 秒，aspect_ratio 须为 adaptive，duration_seconds 须为 -1；输出比例和时长跟随输入。 | The provider classified the task as video editing, but the parameters do not meet its requirements: the input video must be 4–30 seconds, aspect_ratio must be adaptive, and duration_seconds must be -1; output aspect ratio and duration follow the input. | 1 | 192 |
| model_unavailable | The model or endpoint [\w.-]+ does not exist or you do not have access to it&#124;模型未上架 | 所选模型不存在、未上架或当前账户无权访问。 | The selected model does not exist, is not published, or is inaccessible to this account. | 2 | 21, 22 |
| network_timeout | Upstream network error: (?:request&#124;network operation) timed out | 上游网络请求超时，未取得生成结果。 | The upstream network request timed out without a generation result. | 13 | 80, 86, 88, 95, 96, 98, 100, 101, 102, 103, 104, 196, 197 |
| generation_timeout | Upstream model timed out\. Try again later\.&#124;任务处理超时，请稍后重试。&#124;^任务超时$&#124;\[节点\] gemini timeout\.\.\. | 上游生成任务超时，未返回结果。 | The upstream generation timed out without a result. | 52 | 2, 3, 4, 5, 6, 105, 106, 107, 109, 111, 112, 113, 114, 133, 134, 135, 136, 137, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 154, 155, 156, 157, 158, 159, 162, 199, 201, 202, 203, 204, 206, 207, 208, 210, 211, 212, 213, 214, 216, 217 |
| busy | Upstream resources are temporarily busy\. Please try again later\. | 上游资源暂时繁忙。请稍后重试。 | Upstream resources are temporarily busy. Try again later. | 1 | 108 |
| internal_error | The service encountered an unexpected internal error\.&#124;Internal server error, Please contact the administrator&#124;系统异常，请截图联系客服。 | 上游服务发生内部错误。 | The provider encountered an internal error. | 4 | 23, 24, 25, 178 |
| lease_exhausted | 租约超时，已达最大重试次数 | 上游任务租约超时且已耗尽重试次数。 | The upstream task lease expired and exhausted its retries. | 1 | 194 |
| generation_failed | Generation failed\. Please try again\.&#124;任务处理失败，请稍后重试；如持续失败，请联系客服并提供任务ID。&#124;^调用生成服务失败$ | 上游生成失败，未提供具体原因。 | Upstream generation failed without a specific cause. | 6 | 31, 32, 97, 117, 209, 215 |
| no_result | ^生成服务未返回可用结果$ | 上游未返回可用生成结果，具体原因未知。 | The provider returned no usable generation result and no specific cause. | 0 |  |

未匹配行：[161, 174, 175]。这些记录仅有 failed 状态而无错误原因（部分外层为 200/success）；不能据此推断具体原因，沿用原有失败处理。
全空行：[218, 219, 220, 221, 222, 223, 224, 225, 226, 227, 228, 229, 230, 231]，不构造错误。

## MCP 侧文案

以下为本服务自己产生的错误文案（如 request_id 不存在、缺少 request_id、提交结果丢失、超时与网络失败），先按上表匹配上游原因，未命中再按下表匹配。整句翻译（含本服务给出的处理建议，因为 content.message 中已有）；原文为中文的，中文与 content.message 原文一致；原文为英文的（如「Task … not found.」）给出中文译文；{raw}/{detail} 为上游原文或异常文本，原样填入。
submission_lost 的文案为「原因 + 补救说明」：两段都能翻译时拼接返回；原因未命中则整体不添加 message_i18n，避免客户端展示的信息少于模型看到的。

| 规则 ID | 匹配模式（正则） | 中文 | English |
| --- | --- | --- | --- |
| task_not_found | ^Task (?P<task_id>'[^']*'&#124;\"[^\"]*\") not found\.$ | 未找到 request_id 为 {task_id} 的任务。 | No task was found for request_id {task_id}. |
| request_id_missing | ^缺少 request_id： | 缺少 request_id：请传入 generate_* 调用时使用的 request_id（未提供 request_id 的调用则传它返回的 task_id）。 | request_id is missing: pass the request_id used in the generate_* call (for a call that supplied none, pass the task_id it returned). |
| request_id_row_unreadable | ^request_id (?P<task_id>'[^']*'&#124;\"[^\"]*\") 已被占用，但对应的任务行读不回来。 | request_id {task_id} 已被占用，但对应的任务行读不回来。请换一个 request_id 重新提交。 | request_id {task_id} is already in use, but its task record could not be read back. Submit again with a different request_id. |
| no_artifact | ^Task reported success but returned no artifact URLs or inline media$ | 上游报告任务成功，但没有返回任何产物 URL 或内联媒体。 | The provider reported success but returned no artifact URL or inline media. |
| sync_no_artifact | ^同步媒体 API 返回成功，但没有返回任何产物 URL 或内联媒体。 | 同步媒体 API 返回成功，但没有返回任何产物 URL 或内联媒体。 | The synchronous media API reported success but returned no artifact URL or inline media. |
| task_id_unparsed | ^没能从上游响应中解析出 task_id， | 没能从上游响应中解析出 task_id，这次请求的结果无法取回，task_status 也查不到，请直接重新发起。 | No task_id could be parsed from the provider response, so this request's result cannot be retrieved and task_status cannot find it either. Submit the request again. |
| failed_without_message | ^Task failed without error message$ | 任务失败，没有错误信息。 | The task failed without an error message. |
| failed_without_detail | ^Task failed \(upstream reported no error detail\)$ | 任务失败，上游没有提供错误详情。 | The task failed; the provider reported no error detail. |
| failed_unparsed_reason | ^Task failed\. 上游响应（截断）： | 任务失败，没能从上游响应中解析出失败原因。 | The task failed, and no failure reason could be parsed from the provider response. |
| auth_invalid | ^API Token 无效或已过期，请检查 CFGPU_API_TOKEN。$ | API Token 无效或已过期，请检查 CFGPU_API_TOKEN。 | The API token is invalid or expired. Check CFGPU_API_TOKEN. |
| auth_missing | ^缺少 CFGPU API Token： | 缺少 CFGPU API Token：请在请求头携带 Authorization，或设置 CFGPU_API_TOKEN。 | The CFGPU API token is missing: send it in the Authorization header, or set CFGPU_API_TOKEN. |
| auth_provider_missing | ^provider (?P<provider>'[^']*') 缺少凭据：请设置环境变量 (?P<env>[^。]+)。 | provider {provider} 缺少凭据：请设置环境变量 {env}。（该 provider 不使用请求头里的用户 token，也不回退到 CFGPU_API_TOKEN。） | Provider {provider} has no credentials: set the environment variable {env}. (This provider does not use the user token from the request header and does not fall back to CFGPU_API_TOKEN.) |
| rate_limit | ^请求过于频繁，请稍后重试。$ | 请求过于频繁，请稍后重试。 | Too many requests. Try again later. |
| quota_exceeded | ^账户配额不足，请检查余额。$ | 账户配额不足，请检查余额。 | The account quota is insufficient. Check the balance. |
| content_blocked | ^内容被审核拦截，请修改 prompt 后重试。$ | 内容被审核拦截，请修改 prompt 后重试。 | The content was blocked by moderation. Revise the prompt and try again. |
| model_unavailable_mcp | ^所选模型暂不可用，请尝试其他模型。$ | 所选模型暂不可用，请尝试其他模型。 | The selected model is temporarily unavailable. Try another model. |
| connect_timeout | ^(?P<elapsed>[\d.]+)s 内没能与 provider (?P<provider>'[^']*') 建立连接（上游还没收到任何请求），通常是服务端到上游的网络不通。可以稍后重试；如果持续失败，请告诉用户服务端网络需要检查。$ | {elapsed}s 内没能与 provider {provider} 建立连接（上游还没收到任何请求），通常是服务端到上游的网络不通。可以稍后重试；如果持续失败，请告诉用户服务端网络需要检查。 | Could not connect to provider {provider} within {elapsed}s (the provider has received nothing yet); usually the server cannot reach the provider over the network. You can retry later; if it keeps failing, tell the user the server's network needs checking. |
| request_timeout_sent | ^请求超时（已耗时 (?P<elapsed>[\d.]+)s，上限 (?P<budget>[\d.]+)s，provider (?P<provider>'[^']*')）。请求已经发出但没等到回应。$ | 请求超时（已耗时 {elapsed}s，上限 {budget}s，provider {provider}）。请求已经发出但没等到回应。 | The request timed out ({elapsed}s elapsed, limit {budget}s, provider {provider}). It was sent, but no response arrived. |
| request_timeout | ^请求超时（已耗时 (?P<elapsed>[\d.]+)s，上限 (?P<budget>[\d.]+)s，provider (?P<provider>'[^']*')）。请稍后重试。$ | 请求超时（已耗时 {elapsed}s，上限 {budget}s，provider {provider}）。请稍后重试。 | The request timed out ({elapsed}s elapsed, limit {budget}s, provider {provider}). Try again later. |
| network_failed | ^网络请求失败：(?P<detail>.+)$ | 网络请求失败：{detail} | Network request failed: {detail} |
| poll_processing_failed | ^轮询响应处理失败：(?P<detail>.+)$ | 轮询响应处理失败：{detail} | Failed to process the poll response: {detail} |
| invalid_params_raw | ^请求参数错误：(?P<raw>.+)$ | 请求参数错误：{raw} | Invalid request parameters: {raw} |
| task_failed_raw | ^任务执行失败：(?P<raw>.+)$ | 任务执行失败：{raw} | Task execution failed: {raw} |
| submission_lost_raw | ^提交已被上游接受但结果无法取回：(?P<raw>.+)$ | 提交已被上游接受但结果无法取回：{raw} | The provider accepted the submission, but its result cannot be retrieved: {raw} |
| unknown_http | ^未知错误（HTTP (?P<status>\d+)）：(?P<raw>.+)$ | 未知错误（HTTP {status}）：{raw} | Unknown error (HTTP {status}): {raw} |
| submission_lost_remedy | 这次请求的结果没能取回，task_status 也查不到，请直接重新发起。 | 这次请求的结果没能取回，task_status 也查不到，请直接重新发起。 | This request's result could not be retrieved, and task_status cannot find it either. Submit the request again. |
| submission_lost_remedy_understand | 这次请求的回答没能取回，task_status 也查不到，请直接重新调用 understand_vision。如果是耗时过长导致的，可以降低 analysis_depth、缩短视频或收窄问题后再调用。 | 这次请求的回答没能取回，task_status 也查不到，请直接重新调用 understand_vision。如果是耗时过长导致的，可以降低 analysis_depth、缩短视频或收窄问题后再调用。 | This request's answer could not be retrieved, and task_status cannot find it either. Call understand_vision again. If it took too long, lower analysis_depth, shorten the video, or narrow the question before calling again. |

再生成：`python scripts/document_error_normalization.py src/model_generation_tasks_202611131529.csv`。
