#!/usr/bin/env python3
"""
技能打包器 - 将技能文件夹创建为可分发的 .skill 文件

用法：
    python package_skill.py <path/to/skill-folder> [output-directory]

示例：
    python package_skill.py .github/skills/my-skill
    python package_skill.py .github/skills/my-skill ./dist
"""

import sys
import zipfile
from pathlib import Path

# 确保从任何工作目录运行时都能导入同级模块
sys.path.insert(0, str(Path(__file__).parent))
from quick_validate import validate_skill


def package_skill(skill_path, output_dir=None):
    """
    将技能文件夹打包为 .skill 文件。

    参数：
        skill_path: 技能文件夹的路径
        output_dir: .skill 文件的可选输出目录（默认为当前目录）

    返回：
        创建的 .skill 文件路径，出错时返回 None
    """
    skill_path = Path(skill_path).resolve()

    # 验证技能文件夹是否存在
    if not skill_path.exists():
        print(f"❌ 错误：未找到技能文件夹：{skill_path}")
        return None

    if not skill_path.is_dir():
        print(f"❌ 错误：路径不是目录：{skill_path}")
        return None

    # 验证 SKILL.md 是否存在
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        print(f"❌ 错误：在 {skill_path} 中未找到 SKILL.md")
        return None

    # 打包前运行验证
    print("🔍 正在验证技能...")
    valid, message = validate_skill(skill_path)
    if not valid:
        print(f"❌ 验证失败：{message}")
        print("   请在打包前修复验证错误。")
        return None
    print(f"✅ {message}\n")

    # 确定输出位置
    skill_name = skill_path.name
    if output_dir:
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path = Path.cwd()

    skill_filename = output_path / f"{skill_name}.skill"

    # 创建 .skill 文件（zip 格式）
    try:
        with zipfile.ZipFile(skill_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 遍历技能目录
            for file_path in skill_path.rglob('*'):
                if file_path.is_file():
                    # 计算 zip 内的相对路径
                    arcname = file_path.relative_to(skill_path.parent)
                    zipf.write(file_path, arcname)
                    print(f"  已添加：{arcname}")

        print(f"\n✅ 技能已成功打包至：{skill_filename}")
        return skill_filename

    except Exception as e:
        print(f"❌ 创建 .skill 文件时出错：{e}")
        return None


def main():
    if len(sys.argv) < 2:
        print("用法：python package_skill.py <path/to/skill-folder> [output-directory]")
        print("\n示例：")
        print("  python package_skill.py .github/skills/my-skill")
        print("  python package_skill.py .github/skills/my-skill ./dist")
        sys.exit(1)

    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"📦 正在打包技能：{skill_path}")
    if output_dir:
        print(f"   输出目录：{output_dir}")
    print()

    result = package_skill(skill_path, output_dir)

    if result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
