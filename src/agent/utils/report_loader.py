"""src/agent/utils/report_loader.py — 按需加载检查报告原文(DEV_SPEC §4.1.1 ① / ①.5 / ⑨)。

State.exam_reports 只存 `{"file_ref": file_path}`(轻量引用),需要送多模态 LLM 时
才调本模块按需加载,加载结果作为函数局部变量,**不写回 State**(spec §4.1.1
exam_reports 字段说明)。

支持格式(spec §4.1.1 ① Step 3):
- 图片(.jpg / .jpeg / .png / .webp)→ base64 编码,返回 data URI
- PDF(.pdf)→ 返回字节流(由调用方在多模态消息里直传)

未识别后缀 → 抛 ValueError(数据进库时已校验,运行时不应触发)。
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_PDF_SUFFIXES = {".pdf"}


def load_report(file_ref: str) -> dict:
    """按需加载报告文件,返回多模态消息可消费的结构。

    Returns:
        {
          "kind": "image" | "pdf",
          "media_type": "image/jpeg" | "application/pdf" | ...,
          "data": str (image 的 base64 字符串) 或 bytes (pdf 字节流),
          "data_uri": str (image 的 "data:<mt>;base64,<...>" 形式,可直接放进 LangChain image_url 块)
        }

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 后缀既不是图片也不是 PDF
    """
    path = Path(file_ref)
    if not path.exists():
        raise FileNotFoundError(f"report not found: {file_ref}")

    suffix = path.suffix.lower()
    raw = path.read_bytes()

    if suffix in _IMAGE_SUFFIXES:
        media_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        b64 = base64.b64encode(raw).decode("ascii")
        return {
            "kind": "image",
            "media_type": media_type,
            "data": b64,
            "data_uri": f"data:{media_type};base64,{b64}",
        }
    if suffix in _PDF_SUFFIXES:
        return {
            "kind": "pdf",
            "media_type": "application/pdf",
            "data": raw,
            "data_uri": None,  # PDF 不走 data URI,由调用方按 provider 协议组装
        }
    raise ValueError(f"unsupported report file type: {suffix} ({file_ref})")
