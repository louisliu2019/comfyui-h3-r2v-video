---
name: comfyui-h3-r2v-video
description: 通过本地 ComfyUI API 调用 MiniMax-H3 模型做参考图生视频（r2v）/文生视频（T2V）。当用户要求用本地 ComfyUI 生成视频、图生视频、I2V/R2V、按分镜批量出 5 秒短片段、角色一致性视频（挂人物参考图）、或排查 ComfyUI 视频生成报错时使用。依赖 ComfyUI-MiniMaxH3-Easy 节点与 MiniMax-H3 模型文件。
agent_created: true
---

# ComfyUI MiniMax-H3 参考图生视频（r2v）

## Overview

通过 ComfyUI 的 HTTP API（默认 `127.0.0.1:8188`）提交 MiniMax-H3 r2v 工作流：支持 0~9 张参考图（0 张即纯文生视频），输出自带环境音的 mp4，9:16 480x864 / 124帧（≈5.2s@24fps）/ 20 步采样，单条约 7 分钟（RTX 4070 级别显卡）。核心客户端脚本：`scripts/comfy_r2v_client.py`（仅标准库依赖）。

## 前置条件

1. ComfyUI（aki 整合包或标准安装）+ custom node `ComfyUI-MiniMaxH3-Easy`，且 `models/unet` 下有四个模型文件：
   - `minimax_h3_ref2va_pruned_int8_convrot.safetensors`（UNET）
   - `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`（CLIP，type=minimax）
   - `minimax_h3_video_vae_fp16.safetensors` / `minimax_h3_audio_vae_fp32.safetensors`
2. 模型按需动态加载显存（ref2va 约 20GB staged 走内存），12GB 显存可跑。

## 启动 ComfyUI（Windows / aki 整合包）

```bash
# 先探测 8188 是否已在监听, 在跑则直接复用
netstat -ano | grep ":8188" | grep -i LISTEN
# 未运行则后台启动
cd /c/comfyui/ComfyUI && ./venv/Scripts/python.exe main.py --enable-manager --port 8188 > /c/comfyui/comfyui_manual.log 2>&1 &
# 就绪自检(队列空闲即就绪)
python scripts/comfy_r2v_client.py --help   # 或用 wait_ready()
```

## 单条生成（命令行）

```bash
# 纯文生视频（无参考图）
python scripts/comfy_r2v_client.py "东方玄幻仙侠场景。雨夜古城深巷，黑衣剑客靠墙而坐，镜头固定中景" out.mp4

# 参考图生视频（角色一致性：挂人物资产卡）
python scripts/comfy_r2v_client.py \
  "参考图1：谢无咎的形象基准。雨夜深巷，参考图1中的青年男子抬头望雨，写实古装真人剧电影质感" \
  out.mp4 人物资产卡/1.png
```

可选项：`--host` `--width` `--height` `--length`(帧数) `--steps` `--max-wait`(秒)。

## 作为模块批量生成（Python）

```python
import sys; sys.path.insert(0, "<skill目录>/scripts")
from comfy_r2v_client import generate

# 分镜列表: (提示词, [参考图路径], 输出路径)
for prompt, refs, out in shots:
    ok = generate(prompt, refs, out)   # 失败返回 False, 可重试一次
```

批量要点：
- ComfyUI 内部串行执行（同一时刻 1 条 GPU 任务），客户端逐条提交即可，无需自己控并发。
- 每条失败重试 1 次；按输出文件存在性做断点续跑（已存在的片段跳过）。
- 角色一致性：提示词中先声明"参考图N：<角色名>的形象基准"，正文用"参考图N中的<角色>"指代；同时把该角色资产卡 PNG 传给 `ref_images`。

## 角色一致性提示词模板

```
参考图1：{角色名}的形象基准，视频中该角色严格保持其面容、发型与服饰。
{场景与动作描述，用"参考图1中的{角色名}"指代角色}。
{服装/发型细节}，{镜头语言}，写实古装真人剧电影质感，避免动画、动漫、二次元风格
```

经验规则：
- 提示词必须点名角色别名才能触发参考图挂载，只写"青年男子"不会挂卡 → 人物易漂移成动漫风。
- 全局追加风格锚点（如上模板末句），对无参考的空镜同样必要。
- 让模型输出"可读文字"必出乱码——改用"抽象符纹光痕，并非文字"。

## 排错清单（已踩过的坑）

| 症状 | 原因 / 修法 |
|---|---|
| `/prompt` 校验失败 | `SaveVideo` 必须显式传 `format/codec/bit_depth: "auto"` |
| 画面黑屏/VAE 报错 | `VAEDecode`/`VAEDecodeAudio` 的 `samples` 必须接 `SamplerCustomAdvanced` 输出[0]，不能接错节点 |
| 上传图片报 TypeError | multipart body 必须全程 bytes 拼接（f-string 前半段含中文路径时先 `.encode("utf-8")`） |
| 一直排队无输出 | `GET /queue` 看 running/pending；卡死可 `POST /queue {"clear":true}` 清 pending（正在跑的不能取消） |
| 输出文件找不到 | 成片在 ComfyUI output 目录的子文件夹（`filename_prefix` 决定），从 `/history/{prompt_id}` 的 outputs 里取真实路径 |
| 长跑中 worker 冻结、无日志 | 多线程批量脚本里若"持锁调用的函数内部再取同一把锁"，`threading.Lock` 会死锁——改 `RLock`；客户端 print 加 `flush=True`，批量脚本用 `python -u` 启动 |
| 生成中途进程被杀 | ComfyUI 队列会继续跑完当前条，但客户端已死、结果成孤儿文件；重启后按文件存在性重新提交即可 |

## 性能参考

- 480x864 / 124帧 / 20步：采样约 20.5s/it × 20 ≈ 7 分钟/条（含模型动态加载），RTX 4070 12GB。
- 加参考图不会明显变慢；降帧数（124→97）或步数（20→15）可压缩到 ~4-5 分钟，画质略降。
