# -*- coding: utf-8 -*-
"""为「小交付包」准备 embedding 模型（一次性、需要联网）。

为什么单独成脚本
----------------
校赛邮件提交有 50MB 硬上限，390MB 的 embedding 模型无法进包，因此小包只带源码 +
建库源，模型由本脚本在首次部署时显式下载。**绝不把下载塞进 start.bat**：
start.bat 只做「检查 → 启动」，任何下载/安装都会让启动行为的耗时与成败变得不可预期。

产物布局（与生产加载逻辑完全一致，不改任何业务代码）：
    models/hf-cache/hub/models--shibing624--text2vec-base-chinese/
        refs/main, snapshots/<rev>/{model.safetensors,...}
即 HF_HOME=models/hf-cache，sentence-transformers 仍按 repo id
"shibing624/text2vec-base-chinese" 解析，无需任何特殊分支。

下载体积（实测）：
    整仓 snapshot_download           约 1849 MB（含 onnx / openvino / pytorch_model.bin）
    按 ALLOW_PATTERNS 过滤后        约 390 MB（与离线完整包内置的模型一致）
过滤后仍拿不到权重时自动回退整仓下载，因此不会因过滤而装不上模型。

用法：
    python scripts/prepare_model.py            # 缺则下载
    python scripts/prepare_model.py --force    # 强制重下
"""
import argparse
import os
import shutil
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

MODEL_ID = "shibing624/text2vec-base-chinese"
# HF cache 的目录名把 "/" 换成 "--"
MODEL_DIRNAME = "models--" + MODEL_ID.replace("/", "--")
# 只下载 sentence-transformers 加载所必需的文件类型。
# 该仓库整仓约 1.85GB，其中 pytorch_model.bin（约 409MB）、onnx/*.onnx（约 1020MB）、
# openvino/*（约 103MB）本系统从不使用；不过滤会让"准备模型"从约 390MB 膨胀到约 1.85GB。
# 若某仓库只提供 .bin 权重，_download_once() 会自动回退为整仓下载。
ALLOW_PATTERNS = ["*.json", "*.txt", "*.safetensors"]
# 国内网络访问 huggingface.co 常不通，失败后用镜像再试一次
MIRROR_ENDPOINT = "https://hf-mirror.com"

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS_DIR)
HF_HOME = os.path.join(ROOT, "models", "hf-cache")
HUB_DIR = os.path.join(HF_HOME, "hub")
MODEL_DIR = os.path.join(HUB_DIR, MODEL_DIRNAME)


def _find_weights() -> str | None:
    """返回 snapshots/<rev>/model.safetensors 路径；找不到返回 None。"""
    snap_root = os.path.join(MODEL_DIR, "snapshots")
    if not os.path.isdir(snap_root):
        return None
    for rev in sorted(os.listdir(snap_root)):
        for name in ("model.safetensors", "pytorch_model.bin"):
            p = os.path.join(snap_root, rev, name)
            # 交付包内要求是实体文件（不是符号链接）
            if os.path.isfile(p):
                return p
    return None


def _size_mb(path: str) -> float:
    """统计目录真实占用。

    注意：HuggingFace 缓存里 snapshots/ 下的文件是指向 blobs/ 的**符号链接**，
    而 os.path.getsize 会跟随符号链接返回目标大小。若直接累加，同一个权重会被
    算两次（实测把 1849 MB 报成 3698 MB）。这里按 realpath 去重。
    """
    total = 0
    seen = set()
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                real = os.path.realpath(os.path.join(r, f))
                if real in seen:
                    continue
                seen.add(real)
                total += os.path.getsize(real)
            except OSError:
                pass
    return total / 1024 / 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = ap.parse_args()

    print("=" * 68)
    print("  准备 embedding 模型（一次性，需要联网）")
    print("=" * 68)
    print("  模型   :", MODEL_ID)
    print("  目标   :", MODEL_DIR)
    print()

    existing = _find_weights()
    if existing and not args.force:
        print("[SKIP] 模型已就绪，无需下载。")
        print("       权重 :", existing)
        print("       大小 : %.2f MB" % (os.path.getsize(existing) / 1024 / 1024))
        print("       models/hf-cache 合计: %.2f MB" % _size_mb(HF_HOME))
        return 0

    if args.force and os.path.isdir(MODEL_DIR):
        print("[--force] 删除已存在的模型缓存目录 ...")
        shutil.rmtree(MODEL_DIR, ignore_errors=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[错误] 未安装 huggingface_hub。")
        print("       请先执行: backend\\venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt")
        return 1

    os.makedirs(HUB_DIR, exist_ok=True)

    def _download_once():
        """按需下载一次。

        先用 allow_patterns 只取 sentence-transformers 真正需要的文件：
        该仓库整仓约 1.85GB，其中 pytorch_model.bin / onnx/*.onnx / openvino/*
        合计约 1.46GB 本系统一个都用不到（实测整仓下载 1849MB，过滤后约 390MB）。
        若过滤后拿不到权重（极少数仓库只提供 pytorch_model.bin），则回退为整仓下载。
        """
        try:
            p = snapshot_download(repo_id=MODEL_ID, cache_dir=HUB_DIR,
                                  allow_patterns=ALLOW_PATTERNS)
            if _find_weights():
                print("      （只取必要文件：*.json / *.txt / *.safetensors）")
                return p
            print("      （过滤下载后未发现权重文件，回退为整仓下载）")
        except Exception as e:  # noqa: BLE001
            print("      （过滤下载未成功：%s: %s，回退为整仓下载）" % (type(e).__name__, e))
        return snapshot_download(repo_id=MODEL_ID, cache_dir=HUB_DIR)

    attempts = [("默认端点 huggingface.co", None), ("国内镜像 hf-mirror.com", MIRROR_ENDPOINT)]
    last_err = None
    for label, endpoint in attempts:
        if endpoint:
            os.environ["HF_ENDPOINT"] = endpoint
        else:
            os.environ.pop("HF_ENDPOINT", None)
        print("[下载] 尝试 %s ..." % label)
        t0 = time.time()
        try:
            path = _download_once()
            print("[下载] 成功: %s（耗时 %.1f 秒）" % (path, time.time() - t0))
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            print("[下载] 失败: %s: %s" % (type(e).__name__, e))
            print()

    if last_err is not None:
        print("=" * 68)
        print("  [错误] 模型下载失败，两种端点均未成功。")
        print("  最后一次错误:", "%s: %s" % (type(last_err).__name__, last_err))
        print()
        print("  可选的替代做法：")
        print("    1) 在有网/有缓存的机器上执行本脚本，把整个 models\\hf-cache 目录拷过来；")
        print("    2) 或从 ModelScope 下载 shibing624/text2vec-base-chinese，")
        print("       把模型文件放进 snapshots\\<任意版本名>\\ 下（需含 model.safetensors、")
        print("       config.json、vocab.txt、tokenizer_config.json、modules.json、")
        print("       sentence_bert_config.json、1_Pooling\\config.json）；")
        print("    3) 直接使用「离线完整包」——它已内置模型与向量库。")
        print("=" * 68)
        return 1

    weights = _find_weights()
    if not weights:
        print("[错误] 下载结束但未找到权重文件，缓存目录不完整:", MODEL_DIR)
        return 1

    print()
    print("=" * 68)
    print("  模型就绪")
    print("    权重 :", weights)
    print("    大小 : %.2f MB" % (os.path.getsize(weights) / 1024 / 1024))
    print("    models/hf-cache 合计: %.2f MB" % _size_mb(HF_HOME))
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
