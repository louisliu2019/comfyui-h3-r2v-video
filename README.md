# comfyui-h3-r2v-video

本地 ComfyUI API 客户端：调用 **MiniMax-H3** 模型做**参考图生视频（r2v）/ 文生视频（T2V）**，支持 0~9 张参考图做角色一致性，输出自带环境音的竖屏 mp4。

> 本 skill 由 WorkBuddy Agent 在真实 52 集短剧批量生产中沉淀，已跑通 9+ 集 × 8 镜 × 全流程。

## 特性

- **参考图生视频**：挂人物资产卡 PNG，模型自动保持角色面容/发型/服饰一致；不挂参考图即为纯 T2V
- **规格**：9:16 480×864 / 124 帧（≈5.2s @ 24fps）/ 20 步 res_multistep，可 CLI 覆盖
- **自带环境音**：MiniMax-H3 audio VAE 直接出有声视频，无需后期配音氛围
- **零第三方依赖**：仅 Python 标准库
- **断点续跑友好**：返回 True/False + 按输出文件存在性跳过

## 快速开始

```bash
# 1. 启动 ComfyUI（已运行则跳过）
cd /c/comfyui/ComfyUI && ./venv/Scripts/python.exe main.py --port 8188 &

# 2. 纯文生视频
python scripts/comfy_r2v_client.py "东方玄幻仙侠场景。雨夜古城深巷，黑衣剑客靠墙而坐，镜头固定中景" out.mp4

# 3. 参考图生视频（角色一致性）
python scripts/comfy_r2v_client.py \
  "参考图1：谢无咎的形象基准。雨夜深巷，参考图1中的青年男子抬头望雨，写实古装真人剧电影质感" \
  out.mp4 character_card_1.png
```

Python 批量：

```python
from comfy_r2v_client import generate
generate(prompt, ref_images, out_path)  # -> True/False
```

## 模型依赖

`ComfyUI-MiniMaxH3-Easy` 节点 + `models/unet` 下四个文件：

| 文件 | 用途 |
|---|---|
| minimax_h3_ref2va_pruned_int8_convrot.safetensors | UNET |
| qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors | CLIP (type=minimax) |
| minimax_h3_video_vae_fp16.safetensors | 视频 VAE |
| minimax_h3_audio_vae_fp32.safetensors | 音频 VAE |

12GB 显存可跑（模型动态 VRAM 流式加载）。

## 排错

SKILL.md 内含完整踩坑清单：SaveVideo 三件套参数、VAEDecode 连线、multipart bytes 拼接、队列清理、死锁与日志 flush 等。

## 性能

RTX 4070 12GB 实测 ≈ 7 分钟/条；降帧/步数可到 4-5 分钟。
