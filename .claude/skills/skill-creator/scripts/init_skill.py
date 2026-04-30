#!/usr/bin/env python3
"""
技能初始化器 - 从模板创建新技能

用法：
    init_skill.py <skill-name> --path <path>

示例：
    init_skill.py my-new-skill --path .claude/skills
    init_skill.py my-api-helper --path .claude/skills
    init_skill.py custom-skill --path /custom/location
"""

import sys
from pathlib import Path


SKILL_TEMPLATE = """---
name: {skill_name}
description: "[TODO: 完整且详细地说明技能的功能和使用时机。包含何时使用此技能——触发它的具体场景、文件类型或任务。]"
---

# {skill_title}

## 概述

[TODO: 用 1-2 句话说明此技能能做什么]

## 技能结构规划

[TODO: 选择最适合此技能用途的结构。常见模式：

**1. 基于工作流程**（最适合顺序流程）
- 当存在清晰的分步操作流程时效果最好
- 示例：DOCX 技能的"工作流程决策树" → "读取" → "创建" → "编辑"
- 结构：## 概述 → ## 工作流程决策树 → ## 步骤 1 → ## 步骤 2...

**2. 基于任务**（最适合工具集合）
- 当技能提供不同的操作/能力时效果最好
- 示例：PDF 技能的"快速开始" → "合并 PDF" → "拆分 PDF" → "提取文本"
- 结构：## 概述 → ## 快速开始 → ## 任务类别 1 → ## 任务类别 2...

**3. 参考/规范**（最适合标准或规格说明）
- 适用于品牌规范、编码标准或需求文档
- 示例：品牌样式的"品牌规范" → "颜色" → "字体排版" → "功能"
- 结构：## 概述 → ## 规范 → ## 详细说明 → ## 使用方法...

**4. 基于能力**（最适合集成系统）
- 当技能提供多个相互关联的功能时效果最好
- 示例：产品管理的"核心能力" → 编号的能力列表
- 结构：## 概述 → ## 核心能力 → ### 1. 功能 → ### 2. 功能...

模式可以根据需要混合搭配。大多数技能会组合使用多种模式（例如，以基于任务开始，对复杂操作添加工作流程）。

完成后删除整个"技能结构规划"部分——这只是指导说明。]

## [TODO: 根据所选结构替换为第一个主要部分]

[TODO: 在此添加内容。参见现有技能中的示例：
- 技术技能的代码示例
- 复杂工作流程的决策树
- 包含实际用户请求的具体示例
- 根据需要引用 scripts/templates/references]

## 资源

此技能包含示例资源目录，展示如何组织不同类型的捆绑资源：

### scripts/
可直接运行以执行特定操作的可执行代码（Python/Bash 等）。

**其他技能的示例：**
- PDF 技能：`fill_fillable_fields.py`、`extract_form_field_info.py` - PDF 处理工具
- DOCX 技能：`document.py`、`utilities.py` - 文档处理 Python 模块

**适用于：** Python 脚本、shell 脚本或任何执行自动化、数据处理或特定操作的可执行代码。

**注意：** 脚本可以不加载到上下文中直接执行，但智能体仍可能需要读取以进行修补或环境调整。

### references/
用于加载到上下文中以指导智能体处理过程和思考的文档和参考材料。

**其他技能的示例：**
- 产品管理：`communication.md`、`context_building.md` - 详细工作流程指南
- BigQuery：API 参考文档和查询示例
- 财务：模式文档、公司政策

**适用于：** 深度文档、API 参考、数据库模式、综合指南，或智能体在工作时应参考的任何详细信息。

### assets/
不用于加载到上下文中，而是用于智能体产出的输出中的文件。

**其他技能的示例：**
- 品牌样式：PowerPoint 模板文件（.pptx）、logo 文件
- 前端构建器：HTML/React 样板项目目录
- 字体排版：字体文件（.ttf、.woff2）

**适用于：** 模板、样板代码、文档模板、图片、图标、字体，或任何用于复制或在最终输出中使用的文件。

---

**不需要的目录可以删除。** 并非每个技能都需要所有三种类型的资源。
"""

EXAMPLE_SCRIPT = '''#!/usr/bin/env python3
"""
{skill_name} 的示例辅助脚本

这是一个可以直接执行的占位脚本。
根据实际需要替换为真正的实现，或不需要时删除。

其他技能的实际脚本示例：
- pdf/scripts/fill_fillable_fields.py - 填写 PDF 表单字段
- pdf/scripts/convert_pdf_to_images.py - 将 PDF 页面转换为图片
"""

def main():
    print("这是 {skill_name} 的示例脚本")
    # TODO: 在此添加实际的脚本逻辑
    # 可以是数据处理、文件转换、API 调用等。

if __name__ == "__main__":
    main()
'''

EXAMPLE_REFERENCE = """# {skill_title} 的参考文档

这是详细参考文档的占位内容。
根据实际需要替换为真正的参考内容，或不需要时删除。

其他技能的实际参考文档示例：
- product-management/references/communication.md - 状态更新的综合指南
- product-management/references/context_building.md - 收集上下文的深入指南
- bigquery/references/ - API 参考和查询示例

## 参考文档的适用场景

参考文档适用于：
- 全面的 API 文档
- 详细的工作流程指南
- 复杂的多步骤流程
- 对于主 SKILL.md 来说过长的信息
- 仅在特定用例中需要的内容

## 结构建议

### API 参考示例
- 概述
- 认证
- 端点及示例
- 错误代码
- 速率限制

### 工作流程指南示例
- 前置条件
- 分步操作说明
- 常见模式
- 故障排查
- 最佳实践
"""

EXAMPLE_ASSET = """# 示例素材文件

此占位文件代表素材文件的存放位置。
根据实际需要替换为真正的素材文件（模板、图片、字体等），或不需要时删除。

素材文件不用于加载到上下文中，而是用于智能体产出的输出中。

其他技能的素材文件示例：
- 品牌规范：logo.png、slides_template.pptx
- 前端构建器：hello-world/ 目录（包含 HTML/React 样板）
- 字体排版：custom-font.ttf、font-family.woff2
- 数据：sample_data.csv、test_dataset.json

## 常见素材类型

- 模板：.pptx、.docx、样板目录
- 图片：.png、.jpg、.svg、.gif
- 字体：.ttf、.otf、.woff、.woff2
- 样板代码：项目目录、起始文件
- 图标：.ico、.svg
- 数据文件：.csv、.json、.xml、.yaml

注意：这是一个文本占位文件。实际素材可以是任何文件类型。
"""


def title_case_skill_name(skill_name):
    """将连字符分隔的技能名称转换为首字母大写的显示格式。"""
    return ' '.join(word.capitalize() for word in skill_name.split('-'))


def init_skill(skill_name, path):
    """
    初始化一个包含模板 SKILL.md 的新技能目录。

    参数：
        skill_name: 技能名称
        path: 技能目录应创建的路径

    返回：
        创建的技能目录路径，出错时返回 None
    """
    # 确定技能目录路径
    skill_dir = Path(path).resolve() / skill_name

    # 检查目录是否已存在
    if skill_dir.exists():
        print(f"❌ 错误：技能目录已存在：{skill_dir}")
        return None

    # 创建技能目录
    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        print(f"✅ 已创建技能目录：{skill_dir}")
    except Exception as e:
        print(f"❌ 创建目录时出错：{e}")
        return None

    # 从模板创建 SKILL.md
    skill_title = title_case_skill_name(skill_name)
    skill_content = SKILL_TEMPLATE.format(
        skill_name=skill_name,
        skill_title=skill_title
    )

    skill_md_path = skill_dir / 'SKILL.md'
    try:
        skill_md_path.write_text(skill_content, encoding="utf-8")
        print("✅ 已创建 SKILL.md")
    except Exception as e:
        print(f"❌ 创建 SKILL.md 时出错：{e}")
        return None

    # 创建包含示例文件的资源目录
    try:
        # 创建 scripts/ 目录及示例脚本
        scripts_dir = skill_dir / 'scripts'
        scripts_dir.mkdir(exist_ok=True)
        example_script = scripts_dir / 'example.py'
        example_script.write_text(EXAMPLE_SCRIPT.format(skill_name=skill_name), encoding="utf-8")
        print("✅ 已创建 scripts/example.py")

        # 创建 references/ 目录及示例参考文档
        references_dir = skill_dir / 'references'
        references_dir.mkdir(exist_ok=True)
        example_reference = references_dir / 'api_reference.md'
        example_reference.write_text(EXAMPLE_REFERENCE.format(skill_title=skill_title), encoding="utf-8")
        print("✅ 已创建 references/api_reference.md")

        # 创建 assets/ 目录及示例素材占位文件
        assets_dir = skill_dir / 'assets'
        assets_dir.mkdir(exist_ok=True)
        example_asset = assets_dir / 'example_asset.txt'
        example_asset.write_text(EXAMPLE_ASSET, encoding="utf-8")
        print("✅ 已创建 assets/example_asset.txt")
    except Exception as e:
        print(f"❌ 创建资源目录时出错：{e}")
        return None

    # 打印后续步骤
    print(f"\n✅ 技能 '{skill_name}' 已成功初始化于 {skill_dir}")
    print("\n后续步骤：")
    print("1. 编辑 SKILL.md 以完成 TODO 项并更新描述")
    print("2. 自定义或删除 scripts/、references/ 和 assets/ 中的示例文件")
    print("3. 准备就绪后运行验证器检查技能结构")

    return skill_dir


def main():
    if len(sys.argv) < 4 or sys.argv[2] != '--path':
        print("用法：init_skill.py <skill-name> --path <path>")
        print("\n技能名称要求：")
        print("  - kebab-case 标识符（例如 'my-data-analyzer'）")
        print("  - 仅限小写字母、数字和连字符")
        print("  - 最多 64 个字符")
        print("  - 必须与目录名完全一致")
        print("\n示例：")
        print("  init_skill.py my-new-skill --path .claude/skills")
        print("  init_skill.py my-api-helper --path .claude/skills")
        print("  init_skill.py custom-skill --path /custom/location")
        sys.exit(1)

    skill_name = sys.argv[1]
    path = sys.argv[3]

    print(f"🚀 正在初始化技能：{skill_name}")
    print(f"   位置：{path}")
    print()

    result = init_skill(skill_name, path)

    if result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
