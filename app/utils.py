import re

def process_prompt_template(template_string, data):
    """
    根据提供的模板字符串和数据，替换占位符生成最终提示词。

    Args:
        template_string (str): 包含 @[占位符] 的模板字符串.
        data (dict): 包含占位符及其对应值的字典.
                       例如: {'前文': '...', '后文': '...', '提示词': '...', ...}

    Returns:
        str: 替换占位符后的最终提示词.
    """
    processed_prompt = template_string

    # 定义支持的占位符 (根据需要可以调整或从配置/数据库加载)
    supported_placeholders = ['前文', '后文', '提示词', '字数', '风格', '设定']

    # 使用正则表达式查找并替换所有 @[占位符]
    def replace_match(match):
        placeholder = match.group(1) # 获取括号内的占位符名称
        if placeholder in supported_placeholders and placeholder in data:
            # 确保插入的是字符串
            value = data[placeholder]
            if value is None:
                return "" # 如果值为 None，替换为空字符串
            return str(value)
        else:
            # 如果占位符不支持或数据中没有提供，保留原样以便调试
            # 或者可以返回空字符串：return ""
            print(f"警告：模板中的占位符 '@[{placeholder}]' 未在数据中找到或不支持。") # 添加日志或打印
            return match.group(0)

    # 正则表达式匹配 @[任意字符除了]]
    pattern = re.compile(r'@\[([^\]]+)\]')
    processed_prompt = pattern.sub(replace_match, processed_prompt)

    return processed_prompt

# --- 示例用法 (可以注释掉或移除，如果不需要在 utils 文件中直接运行) ---
if __name__ == '__main__':
    template = ("参考@[提示词]，以@[风格]的风格，续写下面的内容：\n"
                "@[前文]\n"
                "请确保字数大约在 @[字数] 字左右。\n"
                "需要考虑以下设定：\n@[设定]\n"
                " 这是一个不存在的占位符 @[未知]\n"
                " 这是一个值为None的占位符 @[空值]")
    user_data = {
        '前文': "故事的开头是这样写的...",
        '后文': "...故事在这里结束。",
        '提示词': "悬疑、惊悚",
        '字数': 500,
        '风格': "黑暗奇幻",
        '设定': "- 主角是一位失忆的侦探。\n- 故事发生在一座哥特式城堡。",
        '空值': None
    }

    final_prompt = process_prompt_template(template, user_data)
    print("----- Final Prompt -----")
    print(final_prompt)
    print("-----------------------")
