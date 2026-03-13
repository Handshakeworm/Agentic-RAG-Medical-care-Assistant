from huggingface_hub import snapshot_download
import os

# 定义模型在 Hugging Face Hub 上的ID
repo_id = "unsloth/Qwen3.5-35B-A3B-GGUF"

# 定义你希望将模型下载到本地的根路径
base_download_dir = "/data/LLMs/Qwen3.5/Qwen3.5-35B-A3B"

# 构建最终的模型下载路径
# 例如，下载到 /data/LLMs/Qwen--Qwen3.5-27B-Chat-AWQ
local_dir = os.path.join(base_download_dir, repo_id.replace("/", "--")) 

print(f"准备下载模型: {repo_id}")
print(f"目标本地路径: {local_dir}")

try:
    # 确保目标目录存在
    os.makedirs(local_dir, exist_ok=True)

    # 使用 snapshot_download 下载模型的所有文件
    downloaded_path = snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=False,
        # ignore_patterns=["*.git*", "*.md"] # 如果需要，可以取消注释并添加忽略模式
    )
    print(f"\n模型已成功下载到: {downloaded_path}")
    print(f"现在你可以使用这个路径来加载 vLLM 模型了。")

except Exception as e:
    print(f"\n模型下载失败: {e}")
    print("请检查以下问题：")
    print("1. 你的网络连接是否正常？")
    print("2. Hugging Face Hub 是否可访问？")
    print("3. 模型ID ('Qwen/Qwen3.5-27B-Chat-AWQ') 是否正确？")
    print(f"4. 你是否有足够的磁盘空间？模型文件会存储在 {local_dir}。")
    print(f"5. 你对目录 {base_download_dir} 是否有写入权限？")
