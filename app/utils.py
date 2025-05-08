import re

def process_prompt_template(template_string, data):
    """
    根据提供的模板字符串和数据，替换占位符生成最终提示词。

    Args:
        template_string (str): 包含 @[占位符] 或 {{占位符}} 的模板字符串.
        data (dict): 包含占位符及其对应值的字典.
                       例如: {'前文': '...', '后文': '...', '提示词': '...', 'markdown指令': '...'}

    Returns:
        str: 替换占位符后的最终提示词.
    """
    processed_prompt = template_string

    # 1. 优先处理新的 @[markdown] 占位符
    # data['markdown指令'] 是由前端 index.html 在启用 Markdown 偏好时发送的
    markdown_template_content = data.get('markdown指令', '') # 如果不存在则默认为空字符串
    processed_prompt = processed_prompt.replace('@[markdown]', markdown_template_content)

    # 2. 处理其他通用的 @[占位符]
    # 定义支持的通用占位符 (从 'markdown指令' 移除，因为它已被特殊处理)
    supported_placeholders = ['前文', '后文', '提示词', '字数', '风格', '设定']

    # 使用正则表达式查找并替换所有剩余的 @[占位符]
    def replace_generic_match(match):
        placeholder = match.group(1) # 获取括号内的占位符名称
        if placeholder == 'markdown': # 已被特殊处理，避免重复或冲突
            return match.group(0) # 如果由于某种原因正则匹配到它，则不替换
        
        if placeholder in supported_placeholders and placeholder in data:
            value = data[placeholder]
            if value is None:
                return "" # 如果值为 None，替换为空字符串
            
            # 对列表类型的 '设定' 进行特殊格式化 (如果需要)
            if placeholder == '设定' and isinstance(value, list):
                # 示例：将设定列表转换为以换行符分隔的字符串
                # 您可以根据需要调整这里的格式化逻辑
                settings_text = "\n".join([f"- {s.get('text', '')}" for s in value if s.get('text') and s.get('enabled', True)])
                return settings_text
            
            return str(value) # 其他情况直接转为字符串
        else:
            print(f"警告：模板中的通用占位符 '@[{placeholder}]' 未在数据中找到或不支持。")
            return match.group(0) # 保留原样

    # 正则表达式匹配 @[任意字符除了]]
    generic_pattern = re.compile(r'@\[([^\]]+)\]')
    processed_prompt = generic_pattern.sub(replace_generic_match, processed_prompt)

    # 3. (可选) 如果您还想支持 {{双大括号}} 占位符，可以在这里添加逻辑
    # 例如，与您之前的 process_prompt_template 版本类似的逻辑：
    # for key, value in data.items():
    #     if key == 'markdown指令': continue # 已处理
    #     placeholder_curly = "{{" + key + "}}"
    #     if isinstance(value, list) and key == '设定':
    #         settings_text = "\n".join([f"- {s['text']}" for s in value if s.get('text') and s.get('enabled', True)]
    #         processed_prompt = processed_prompt.replace(placeholder_curly, settings_text)
    #     elif isinstance(value, (str, int, float)):
    #         processed_prompt = processed_prompt.replace(placeholder_curly, str(value))

    return processed_prompt

# --- 示例用法 (可以注释掉或移除，如果不需要在 utils 文件中直接运行) ---
if __name__ == '__main__':
    template_sys = (
        "请根据以下信息创作：\n"
        "主要内容：@[提示词]\n"
        "风格：@[风格]\n"
        "前文参考：\n@[前文]\n"
        "设定细节：\n@[设定]\n"
        "字数：@[字数]\n"
        "Markdown格式要求：\n@[markdown]\n"
        "不存在的占位符：@[未知占位符]"
    )
    user_input_data = {
        '前文': "夜幕降临，古堡的钟声敲响了十二下。",
        '提示词': "写一个关于吸血鬼伯爵和女猎人的禁忌之恋的故事。",
        '风格': "哥特式浪漫",
        '字数': "约1000字",
        '设定': [
            {'text': "伯爵拥有魅惑人心的能力，但惧怕阳光和银器。", 'enabled': True},
            {'text': "女猎人家族世代以猎杀吸血鬼为己任，身手矫健。", 'enabled': True},
            {'text': "故事发生在一个偏远的、终年被迷雾笼罩的山区。", 'enabled': True},
            {'text': "一个被遗忘的预言暗示了两人的命运纠葛。", 'enabled': False} # 这个设定不会被包括
        ],
        'markdown指令': "请使用Markdown。对话使用粗体，内心独白使用斜体。重要的场景转折使用三级标题。",
        # 'markdown指令': "" # 测试markdown指令为空的情况
    }

    final_processed_prompt = process_prompt_template(template_sys, user_input_data)
    print("----- 处理后的提示词 (处理 @[markdown] 和其他 @[占位符]) -----")
    print(final_processed_prompt)
    print("--------------------------------------------------------")

    template_with_curly = (
        "Curly Test: {{提示词}}, Style: {{风格}}. Markdown: @[markdown]"
    )
    # 如果要测试 {{}}，需要取消上面 process_prompt_template 中步骤3的注释
    # final_curly_prompt = process_prompt_template(template_with_curly, user_input_data)
    # print("----- 处理后的提示词 (包括 {{}} 和 @[markdown]) -----")
    # print(final_curly_prompt)
    # print("-------------------------------------------------")
