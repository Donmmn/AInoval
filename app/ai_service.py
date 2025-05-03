from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app import db
from app.models.ai_service import AIService
import requests
from flask import jsonify

ai_service = Blueprint('ai_service', __name__, url_prefix='/ai_service')

@ai_service.route('/manage', methods=['GET', 'POST'])
@login_required
def manage():
    # TODO: Separate System vs User AI Services later
    # For now, just show user's services
    configs = AIService.query.filter_by(owner_id=current_user.id, is_system_service=False).all()
    
    # Also get system services (only name usually)
    system_services = AIService.query.filter_by(is_system_service=True).all()
    
    # ---> Pass admin status to the template
    is_admin = current_user.is_admin 
    
    if request.method == 'POST':
        # Simplified based on new model
        name = request.form.get('name')
        service_type = request.form.get('service_type')
        api_key = request.form.get('api_key') # Handle sensitive data carefully!
        base_url = request.form.get('base_url')
        model_name = request.form.get('model_name')
        
        if not name or not service_type: # Basic validation
             flash('名称和服务类型是必填项')
             return redirect(url_for('ai_service.manage'))

        new_config = AIService(
            name=name,
            service_type=service_type,
            api_key=api_key, # Again, handle with care
            base_url=base_url,
            model_name=model_name,
            is_system_service=False, # User adding their own service
            owner_id=current_user.id
        )
        db.session.add(new_config)
        db.session.commit()
        flash('自定义AI服务配置已添加')
        return redirect(url_for('ai_service.manage'))
        
    # Pass all configs and admin status to the template
    return render_template(
        'ai_service_manage.html', 
        user_configs=configs, 
        system_configs=system_services,
        is_admin=is_admin,
        active_config_id=current_user.active_ai_service_id
    )

@ai_service.route('/delete/<int:config_id>')
@login_required
def delete(config_id):
    config = AIService.query.get(config_id)
    # Allow deletion only if user owns it AND it's not a system service
    if config and config.owner_id == current_user.id and not config.is_system_service:
        db.session.delete(config)
        db.session.commit()
        flash('配置已删除')
    # TODO: Add admin deletion logic later if needed
    elif current_user.is_admin and config and config.is_system_service:
         # Admin deleting system service? Needs confirmation / careful handling
         flash('删除系统服务的功能暂未实现') # Placeholder
         pass
    else:
        flash('无权限或配置不存在')
    return redirect(url_for('ai_service.manage')) 

def call_ai_service(prompt: str, config_id: int):
    """
    Calls the specified AI service configuration with the given prompt.

    Args:
        prompt: The user's prompt (already processed by a template).
        config_id: The ID of the AIService configuration to use.

    Returns:
        A dictionary containing the AI response or an error message.
    """
    # 需要在函数内部访问数据库模型 AIService
    from .models.ai_service import AIService 
    service_config = AIService.query.get(config_id)

    # 1. 验证服务配置是否存在以及用户是否有权限
    if not service_config:
        return {"error": "AI 服务配置未找到"}

    # 导入 current_user 以进行权限检查
    from flask_login import current_user 
    
    # 检查用户是否已登录并且具有 id 属性
    # 注意：这假定 call_ai_service 在 current_user 可用的请求上下文中被调用
    user_owns_service = hasattr(current_user, 'id') and not current_user.is_anonymous and service_config.owner_id == current_user.id
    is_accessible = service_config.is_system_service or user_owns_service
    
    if not is_accessible:
         # 检查用户是未登录还是仅无权访问
         if not hasattr(current_user, 'id') or current_user.is_anonymous:
             return {"error": "用户未登录，无法访问 AI 服务"}
         else:
             return {"error": f"无权使用 AI 服务配置 '{service_config.name}'"}
    
    # 2. 获取必要的配置信息
    api_key = service_config.api_key
    base_url = service_config.base_url
    model_name = service_config.model_name
    service_type = service_config.service_type # 用于区分不同的API格式

    if not base_url:
        return {"error": f"服务 '{service_config.name}' (ID: {config_id}) 未配置 Base URL"}
    if not model_name:
         return {"error": f"服务 '{service_config.name}' (ID: {config_id}) 未配置 Model Name"}
        
    # 3. 构建请求 Headers
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
         # TODO: 根据服务类型适配不同的认证方式
         headers["Authorization"] = f"Bearer {api_key}" 
    elif not service_config.is_system_service: 
        print(f"警告：用户服务 '{service_config.name}' (ID: {config_id}) 缺少 API Key")

    # 4. 构建请求 Body (Payload) 和 API 端点 - 需要根据 service_type 适配
    payload = {}
    api_endpoint = None 

    try:
        # --- OpenAI/DeepSeek/Groq/Ollama (Chat Completions API 兼容) ---
        if service_type in ['openai', 'deepseek', 'custom_openai_compatible', 'groq', 'ollama']: 
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "user", "content": prompt} 
                ],
            }
            processed_base_url = base_url.rstrip('/') 
            api_path = "/v1/chat/completions" # 默认路径
            
            if service_type == 'ollama':
                 # 如果 base_url 不包含 /v1 (常见于本地 Ollama)，使用 /api/chat
                 if '/v1' not in processed_base_url: 
                      api_path = "/api/chat"
                 else: # 如果 base_url 包含 /v1，则假定兼容 OpenAI 路径
                     api_path = "/chat/completions" # 例如 base_url = http://host/v1

            api_endpoint = f"{processed_base_url}{api_path}"
            
        # --- 在此添加其他 service_type 的处理逻辑 ---
        # elif service_type == 'anthropic':
        #    ... 
        
        else:
            return {"error": f"不支持的服务类型: {service_type}"}

        if not api_endpoint:
             return {"error": "未能为支持的服务类型确定 API 端点"} 
             
        # 5. 发送请求
        print(f"调用 AI 服务: ID={config_id}, Type='{service_type}', Endpoint='{api_endpoint}', Model='{model_name}'") 
        response = requests.post(api_endpoint, headers=headers, json=payload, timeout=180) 
        response.raise_for_status() 

        # 6. 解析响应
        response_data = response.json()
        
        # --- 解析 OpenAI/兼容格式响应 ---
        if service_type in ['openai', 'deepseek', 'custom_openai_compatible', 'groq', 'ollama']:
            if "choices" in response_data and len(response_data["choices"]) > 0:
                first_choice = response_data["choices"][0]
                if "message" in first_choice and "content" in first_choice["message"]:
                     ai_content = first_choice["message"]["content"]
                     print(f"AI 响应成功接收: {ai_content[:100]}...") 
                     return {"success": True, "content": ai_content}
            
            print(f"AI 服务 {service_type} (ID: {config_id}) 返回了意外的响应结构: {response_data}") 
            error_message = "AI 响应格式不符合预期"
            # 尝试从常见错误结构中提取信息
            if isinstance(response_data.get("error"), dict) and "message" in response_data["error"]:
                 error_message = response_data["error"]["message"]
            elif "detail" in response_data: 
                 error_message = response_data["detail"]
            return {"error": error_message}
        
        # --- 在此添加其他 service_type 的响应解析 ---
        # elif service_type == 'anthropic':
        #    ...

        else: 
             print(f"未实现对服务类型 '{service_type}' 的响应解析")
             return {"error": f"未实现对服务类型 '{service_type}' 的响应解析"}

    except requests.exceptions.Timeout:
         error_msg = f"调用 AI 服务 '{service_config.name}' (ID: {config_id}) 超时 (180秒)"
         print(error_msg)
         return {"error": error_msg}
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        status_code = e.response.status_code if e.response is not None else "N/A"
        response_text = ""
        if e.response is not None:
            try:
                error_data = e.response.json()
                if isinstance(error_data.get("error"), dict) and "message" in error_data["error"]: error_detail = error_data["error"]["message"]
                elif "detail" in error_data: error_detail = error_data["detail"]
                elif "message" in error_data: error_detail = error_data["message"]
                else: response_text = e.response.text
            except ValueError: response_text = e.response.text

        full_error_msg = f"调用 AI 服务 '{service_config.name}' (ID: {config_id}) 出错。状态码: {status_code}. 详情: {error_detail}"
        if response_text: full_error_msg += f". 原始响应: {response_text[:500]}"
        print(full_error_msg)
        # 返回给前端的错误需要考虑是否暴露过多细节
        user_facing_error = f"调用 AI 服务时出错: {error_detail}" 
        if "API key" in error_detail: # 简单过滤 API Key 错误
            user_facing_error = "AI 服务认证失败或配置错误"
        return {"error": user_facing_error} 
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"处理 AI 服务 '{service_config.name}' (ID: {config_id}) 响应时发生未知错误: {error_trace}")
        return {"error": f"处理 AI 响应时发生内部错误: {str(e)}"} 