# -*- coding: utf-8 -*-
"""ComfyUI MiniMax-H3 r2v (参考图生视频) 客户端

用法:
  单测/独立调用:
    python comfy_r2v_client.py "提示词" [ref1.png ref2.png ...] out.mp4 [选项]

  作为模块:
    from comfy_r2v_client import generate
    generate(prompt, ref_images, out_path) -> bool

依赖: 仅 Python 标准库。要求 ComfyUI 已在 HOST 运行, 且 models/unet 下有:
  - minimax_h3_ref2va_pruned_int8_convrot.safetensors (UNET)
  - qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors (CLIPLoader type=minimax)
  - minimax_h3_video_vae_fp16.safetensors / minimax_h3_audio_vae_fp32.safetensors (VAE)
节点依赖: ComfyUI-MiniMaxH3-Easy (MiniMaxH3ReferenceToVideo), CreateVideo/SaveVideo (ComfyUI 内置 video)。
"""
import os, sys, json, time, random, shutil, urllib.request, uuid, argparse

# ---------------- 默认参数(可被 CLI 覆盖) ----------------
HOST = os.environ.get("COMFY_HOST", "http://127.0.0.1:8188")
UNET = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE_V = "minimax_h3_video_vae_fp16.safetensors"
VAE_A = "minimax_h3_audio_vae_fp32.safetensors"
WIDTH, HEIGHT, LENGTH, STEPS = 480, 864, 124, 20   # 9:16, 124帧≈5.2s@24fps
FPS = 24.0
OUT_DIR = os.environ.get("COMFY_OUT", r"C:\comfyui\ComfyUI\output")

def _post_json(url, obj, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def upload_image(local_path, host=None):
    """上传参考图到 ComfyUI input 目录, 返回服务端文件名"""
    host = host or HOST
    fn = f"ref_{uuid.uuid4().hex[:8]}_{os.path.basename(local_path)}"
    boundary = uuid.uuid4().hex
    with open(local_path, "rb") as f:
        data = f.read()
    # 注意: multipart body 必须全程 bytes 拼接, 混入 str 会 TypeError
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
           ).encode("utf-8") + data + f"\r\n--{boundary}\r\n".encode("utf-8")
    body += (f"Content-Disposition: form-data; name=\"type\"\r\n\r\n"
             f"input\r\n--{boundary}--\r\n").encode("utf-8")
    req = urllib.request.Request(f"{host}/upload/image", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["name"]

def build_workflow(prompt, ref_names, seed, prefix,
                   width=WIDTH, height=HEIGHT, length=LENGTH, steps=STEPS):
    """构建 r2v API 工作流。ref_names 为空列表时退化为纯文生视频(T2V)。
    SaveVideo 必须显式传 format/codec/bit_depth='auto', 否则节点校验失败。"""
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_V}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_A}},
        "5": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "7": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "8": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["10", 0]}},
        "10": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0],
            "prompt": prompt, "width": width, "height": height, "length": length,
            "ref_image_size": "match"}},
        # 12/13 的 samples 必须接 SamplerCustomAdvanced[0] (节点14), 不能接 VAE/自己
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["14", 0], "vae": ["4", 0]}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["6", 0], "guider": ["8", 0], "sampler": ["5", 0],
            "sigmas": ["7", 0], "latent_image": ["10", 1]}},
        "15": {"class_type": "CreateVideo", "inputs": {
            "images": ["12", 0], "fps": FPS, "audio": ["13", 0], "color_space": "sRGB"}},
        "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": prefix,
            "format": "auto", "codec": "auto", "bit_depth": "auto"}},
    }
    for i, name in enumerate(ref_names or []):
        wf[f"2{i}0"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        # COMFY_AUTOGROW_V3: 向 dynamic combo 输入 ref_images 追加连接
        wf["10"]["inputs"].setdefault("ref_images", []).append([f"2{i}0", 0])
    return {"prompt": wf, "client_id": "r2v-client"}

def generate(prompt, ref_images, out_path, max_wait=1800, host=None,
             width=WIDTH, height=HEIGHT, length=LENGTH, steps=STEPS, prefix="r2v"):
    """提交工作流并轮询直到完成, 从 ComfyUI output 拷贝成片到 out_path。
    ref_images: 本地参考图路径列表(0~9张); 成功返回 True"""
    host = host or HOST
    refs = [upload_image(p, host) for p in (ref_images or [])]
    seed = random.randint(0, 2**48)
    r = _post_json(f"{host}/prompt", build_workflow(
        prompt, refs, seed, prefix, width, height, length, steps))
    pid = r["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < max_wait:
        time.sleep(10)
        with urllib.request.urlopen(f"{host}/history/{pid}", timeout=15) as fh:
            hist = json.load(fh)
        if pid not in hist:
            continue  # 排队/执行中
        h = hist[pid]
        st = h.get("status", {})
        if st.get("status_str") == "error":
            print(f"[r2v] 执行失败 {os.path.basename(out_path)}: "
                  + str(st.get("messages", []))[:500], flush=True)
            return False
        vids = []
        for node_out in h.get("outputs", {}).values():
            for k in ("videos", "gifs", "images"):
                for item in node_out.get(k, []):
                    vids.append(item)
        if vids:
            sub = vids[0].get("subfolder", "")
            src = os.path.join(OUT_DIR, sub, vids[0]["filename"])
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            shutil.copyfile(src, out_path)
            print(f"[r2v] 完成 {os.path.basename(out_path)} ({time.time()-t0:.0f}s)", flush=True)
            return True
    print(f"[r2v] 超时 {os.path.basename(out_path)}", flush=True)
    return False

def wait_ready(host=None, timeout=3600):
    """等待 ComfyUI 服务就绪且队列空闲(适合开机后自检)"""
    host = host or HOST
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{host}/queue", timeout=5) as r:
                q = json.load(r)
            if not q.get("queue_running") and not q.get("queue_pending"):
                return True
        except Exception:
            pass
        time.sleep(15)
    return False

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ComfyUI MiniMax-H3 r2v 参考图生视频")
    ap.add_argument("prompt")
    ap.add_argument("out")                      # 输出 mp4 路径
    ap.add_argument("refs", nargs="*", default=[])  # 参考图(0~9张)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--height", type=int, default=HEIGHT)
    ap.add_argument("--length", type=int, default=LENGTH, help="帧数, 124≈5.2s@24fps")
    ap.add_argument("--steps", type=int, default=STEPS)
    ap.add_argument("--max-wait", type=int, default=1800)
    a = ap.parse_args()
    ok = generate(a.prompt, a.refs, a.out, a.max_wait, a.host,
                  a.width, a.height, a.length, a.steps)
    sys.exit(0 if ok else 1)
