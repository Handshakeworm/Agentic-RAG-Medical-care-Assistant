import logging
from vllm import LLM, SamplingParams

# 屏蔽 vLLM 引擎子进程退出时的误报警告
logging.getLogger("vllm.v1.engine.core_client").setLevel(logging.CRITICAL)


if __name__ == '__main__':
    model_path = "/data/LLMs/Qwen3.5/Qwen3.5-35B-A3B/cyankiwi--Qwen3.5-35B-A3B-AWQ-4bit"

    print(f"正在使用 vLLM 加载量化模型: {model_path}")

    try:
        llm = LLM(
            model=model_path,
            dtype="float16",
            gpu_memory_utilization=0.85,
            max_model_len=2048,
            language_model_only=True,   # 跳过视觉编码器，纯文本模式（官方推荐）
        )

        print("模型加载成功！")

        # 使用 chat template 格式化 prompt
        tokenizer = llm.get_tokenizer()
        messages = [{"role": "user", "content": "你好，请简单介绍一下你自己。"}]
        prompt = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=True,  # 开启思维链
        )

        sampling_params = SamplingParams(
            temperature=0.7,
            top_p=0.9,
            max_tokens=512,
            stop=["<|im_end|>"],  # 遇到结束符停止
        )
        outputs = llm.generate([prompt], sampling_params)

        for output in outputs:
            print(f"Prompt: 你好，请简单介绍一下你自己。")
            print(f"Generated: {output.outputs[0].text.strip()}")

        del llm  # 正确释放引擎资源

    except Exception as e:
        print(f"加载模型时发生错误: {e}")
        import traceback
        traceback.print_exc()

    print("程序结束。")
