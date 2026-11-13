# Generate 上游错误归一化表

来源：`src/model_generation_tasks_202611131529.csv`，共 230 条记录。
命中明确原因 213 条；空记录 14 条；未匹配 3 条。

generate_image / generate_video / generate_audio / task_status / task_wait 的 MCP 错误响应保留原有 content.message；命中时仅通过 structuredContent.message_i18n 返回 {zh, en} 两种归一化文案。
不改 tasks.error、原始响应、error_type、retryable 或关联 ID；非终态轮询 last_error 和非 MCP 调用保持原有文案。未命中时不添加 message_i18n。
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

再生成：`python scripts/document_error_normalization.py src/model_generation_tasks_202611131529.csv`。
