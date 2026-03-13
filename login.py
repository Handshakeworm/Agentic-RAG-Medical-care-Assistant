# hf_login.py
from huggingface_hub import login
import os

# 从环境变量中获取 token，或者直接在这里粘贴你的 token
# 推荐从环境变量获取，更安全
hf_token = os.environ.get("HF_TOKEN")

if not hf_token:
    # 如果环境变量中没有，就提示用户手动输入
    print("请输入你的 Hugging Face Token：")
    hf_token = input()

try:
    login(token=hf_token)
    print("\nHugging Face 登录成功！你的 token 已被保存。")
    print("现在你可以尝试运行你的模型下载脚本了。")
except Exception as e:
    print(f"\nHugging Face 登录失败: {e}")
    print("请确保你的 token 是正确的，并且拥有访问 Qwen 模型的权限。")
