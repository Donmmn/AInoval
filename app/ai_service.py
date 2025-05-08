from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app import db
from app.models.ai_service import AIService
import requests
from flask import jsonify
import json

print("--- LOADING app/ai_service.py (Top Level) ---") # <-- 添加顶级打印

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

def call_ai_service(prompt: str, config_id: int = None, enable_streaming: bool = False, 
                    # Add optional pre-fetched config details for streaming:
                    config_details: dict = None,
                    # Add optional dictionary to store token info
                    token_info: dict = None): 
    print("--- INSIDE NEW call_ai_service FUNCTION (with token_info) --- ") 
    """
    Calls the specified AI service configuration with the given prompt.
    Supports both regular and streaming responses.

    Args:
        prompt: The user's prompt.
        config_id: The ID of the AIService configuration (used if not streaming).
        enable_streaming: If True, attempt to get a streaming response.
        config_details: If streaming, a dict containing pre-fetched config 
                        (api_key, base_url, model_name, service_type).
                        Required if enable_streaming is True.
        token_info: If streaming, an optional dictionary that will be updated 
                    with {'total': total_tokens_consumed}.

    Returns/Yields:
        If streaming enabled: Generator yielding text chunks.
        If streaming disabled: Dictionary with response or error.
    """
    # Needs access to the AIService model definition
    from .models.ai_service import AIService

    service_config = None
    api_key = None
    base_url = None
    model_name = None
    service_type = None
    config_name_for_error = f"ID: {config_id}" # Default error identifier

    if enable_streaming:
        # --- Streaming Path: Use pre-fetched details --- 
        if not config_details:
            raise ValueError("config_details are required when enable_streaming is True")
        
        api_key = config_details.get('api_key')
        base_url = config_details.get('base_url')
        model_name = config_details.get('model_name')
        service_type = config_details.get('service_type')
        # Use the name from details for better error messages if available
        if config_details.get('name'): 
             config_name_for_error = f"'{config_details['name']}'"
             
        # Basic validation of passed details
        if not base_url: raise ValueError(f"Service {config_name_for_error} 未配置 Base URL")
        if not model_name: raise ValueError(f"Service {config_name_for_error} 未配置 Model Name")
        if not service_type: raise ValueError(f"Service {config_name_for_error} 无效的服务类型")
        
        # Permission check should have happened in the API route before calling
        
    else:
        # --- Non-Streaming Path: Fetch config from DB --- 
        if config_id is None:
             return {"error": "config_id is required when enable_streaming is False"}
             
        service_config = AIService.query.get(config_id)
        if not service_config:
            return {"error": "AI 服务配置未找到"}
        config_name_for_error = f"'{service_config.name}' (ID: {config_id})" # Use actual name
            
        # Permission Check (needs app context here)
        user_owns_service = hasattr(current_user, 'id') and not current_user.is_anonymous and service_config.owner_id == current_user.id
        is_accessible = service_config.is_system_service or user_owns_service
        if not is_accessible:
            error_msg = "用户未登录或无权访问 AI 服务"
            if hasattr(current_user, 'id') and not current_user.is_anonymous:
                error_msg = f"无权使用 AI 服务配置 {config_name_for_error}"
            return {"error": error_msg}
            
        api_key = service_config.api_key
        base_url = service_config.base_url
        model_name = service_config.model_name
        service_type = service_config.service_type
        
        if not base_url: return {"error": f"服务 {config_name_for_error} 未配置 Base URL"}
        if not model_name: return {"error": f"服务 {config_name_for_error} 未配置 Model Name"}

    # --- Common Logic (Headers, Payload, Request, Response Handling) --- 
    headers = {"Content-Type": "application/json"}
    if api_key: headers["Authorization"] = f"Bearer {api_key}"
    elif not enable_streaming and not service_config.is_system_service: 
        # Only warn if not streaming and it's a user service
        print(f"警告：用户服务 {config_name_for_error} 缺少 API Key")

    payload = {}
    api_endpoint = None
    stream_param = {"stream": True} if enable_streaming else {} 

    try:
        # --- Payload and Endpoint Construction (Remains largely the same) --- 
        if service_type in ['openai', 'deepseek', 'custom_openai_compatible', 'groq', 'ollama']:
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                **stream_param 
            }
            # For OpenAI compatible services, request usage statistics in the stream
            if enable_streaming and service_type in ['openai', 'deepseek', 'custom_openai_compatible', 'groq']:
                payload["stream_options"] = {"include_usage": True}
                print(f"[Stream Debug] Added stream_options for {service_type}")

            processed_base_url = base_url.rstrip('/')
            api_path = "/v1/chat/completions"
            if service_type == 'ollama':
                 if '/v1' not in processed_base_url: api_path = "/api/chat"
                 else: api_path = "/chat/completions"
            api_endpoint = f"{processed_base_url}{api_path}"
        else:
            error_msg = f"不支持的服务类型: {service_type}"
            if enable_streaming: raise TypeError(error_msg)
            return {"error": error_msg}

        if not api_endpoint:
            error_msg = "未能为支持的服务类型确定 API 端点"
            if enable_streaming: raise ValueError(error_msg)
            return {"error": error_msg}

        # --- Request Sending (Remains the same) --- 
        print(f"调用 AI 服务: ({config_name_for_error}), Type='{service_type}', Endpoint='{api_endpoint}', Model='{model_name}', Streaming={enable_streaming}")
        response = requests.post(
             api_endpoint, 
             headers=headers, 
             json=payload, 
             timeout=180, 
             stream=enable_streaming 
        )
        response.raise_for_status() 

        # 6. 处理响应 (区分流式和非流式)
        if enable_streaming:
            # --- 流式响应处理 --- 
            
            # Define an inner generator that will also store token counts
            def _stream_generator_with_tokens():
                # Initialize local counters, we'll update the passed dict at the end
                _local_total_tokens = 0
                _local_prompt_tokens = 0
                _local_completion_tokens = 0
                
                print(f"AI 服务 ({config_name_for_error}) 开始流式传输响应...")
                chunk_counter = 0 
                try:
                    for line in response.iter_lines():
                        if line:
                            decoded_line = line.decode('utf-8')
                            # print(f"[Stream Debug] Received line: {decoded_line}") # Can be very verbose
                            if decoded_line.startswith('data: '):
                                json_str = decoded_line[len('data: '):]
                                if json_str.strip() == '[DONE]':
                                    print(f"[Stream Debug] Received [DONE] marker for {config_name_for_error}.")
                                    break
                                try:
                                    chunk_data = json.loads(json_str)
                                    # print(f"[Stream Debug] Parsed JSON chunk: {chunk_data}")

                                    # Check for usage information (OpenAI compatible with stream_options)
                                    if "usage" in chunk_data and isinstance(chunk_data["usage"], dict):
                                        usage = chunk_data["usage"]
                                        # Update local counters from usage chunk
                                        _local_prompt_tokens = usage.get("prompt_tokens", _local_prompt_tokens)
                                        _local_completion_tokens = usage.get("completion_tokens", _local_completion_tokens)
                                        _local_total_tokens = usage.get("total_tokens", 
                                            _local_prompt_tokens + _local_completion_tokens)
                                        print(f"[Stream Debug] Usage found in chunk for {config_name_for_error}: Prompt={_local_prompt_tokens}, Completion={_local_completion_tokens}, Total={_local_total_tokens}")
                                    
                                    # Extract content
                                    if service_type in ['openai', 'deepseek', 'custom_openai_compatible', 'groq', 'ollama']:
                                        if chunk_data.get("choices") and len(chunk_data["choices"]) > 0:
                                            delta = chunk_data["choices"][0].get("delta")
                                            if delta and "content" in delta:
                                                content_chunk = delta["content"]
                                                if content_chunk:
                                                    # print(f"[Stream Debug] Yielding chunk #{chunk_counter}: {content_chunk[:50]}...")
                                                    yield content_chunk
                                                    chunk_counter += 1
                                        elif chunk_data.get('done', False) and service_type == 'ollama':
                                            # For Ollama, 'done' message often contains final token counts
                                            # Update local counters from Ollama's done message
                                            _local_prompt_tokens = chunk_data.get('prompt_eval_count', _local_prompt_tokens)
                                            _local_completion_tokens = chunk_data.get('eval_count', _local_completion_tokens)
                                            _local_total_tokens = _local_prompt_tokens + _local_completion_tokens
                                            print(f"[Stream Debug] Ollama stream finished (done=True) for {config_name_for_error}. Tokens: P={_local_prompt_tokens}, C={_local_completion_tokens}, Total={_local_total_tokens}")
                                            break # Stop iteration on Ollama's done message
                                        # else:
                                            # print(f"[Stream Debug] Unknown chunk structure (not choices/delta): {chunk_data}")
                                    # else:
                                        # print(f"[Stream Debug] Service type '{service_type}' stream parsing not implemented for content.")
                                except json.JSONDecodeError:
                                    print(f"[Stream Debug] JSONDecodeError for line: {json_str} in {config_name_for_error}")
                    # --- After the loop, update the passed token_info dictionary --- 
                    if token_info is not None:
                        token_info['total'] = _local_total_tokens
                        # Optionally add prompt/completion tokens too if needed later
                        # token_info['prompt'] = _local_prompt_tokens
                        # token_info['completion'] = _local_completion_tokens
                        print(f"[Stream Info] Updated token_info dict: {token_info}")
                    else:
                        print(f"[Stream Warning] token_info dictionary was not provided.")

                    print(f"AI 服务 ({config_name_for_error}) 流处理完成. Yielded {chunk_counter} content chunks. Final recorded tokens: Total={_local_total_tokens}")
                except Exception as e:
                    print(f"!!! Error during response iteration for AI service {config_name_for_error}: {e}")
                    # Optionally re-raise or yield an error marker
            
            return _stream_generator_with_tokens() # Return the generator instance
        else:
            # --- 非流式响应处理 --- 
            response_data = response.json()
            if service_type in ['openai', 'deepseek', 'custom_openai_compatible', 'groq', 'ollama']:
                 if "choices" in response_data and len(response_data["choices"]) > 0:
                     first_choice = response_data["choices"][0]
                     if "message" in first_choice and "content" in first_choice["message"]:
                          ai_content = first_choice["message"]["content"]
                          print(f"AI 响应成功接收: {ai_content[:100]}...")
                          return {"success": True, "content": ai_content}
                 print(f"AI 服务 {service_type} ({config_name_for_error}) 返回了意外的响应结构: {response_data}")
                 error_message = "AI 响应格式不符合预期"
                 if isinstance(response_data.get("error"), dict) and "message" in response_data["error"]: error_message = response_data["error"]["message"]
                 elif "detail" in response_data: error_message = response_data["detail"]
                 return {"error": error_message}
            else: return {"error": f"未实现对服务类型 '{service_type}' 的响应解析"}
            
    # --- Exception Handling (Remains largely the same, uses config_name_for_error) --- 
    except requests.exceptions.Timeout as e:
         error_msg = f"调用 AI 服务 {config_name_for_error} 超时 (180秒)"
         print(error_msg)
         if enable_streaming: raise TimeoutError(error_msg) from e
         return {"error": error_msg}
    except requests.exceptions.RequestException as e:
        # ... (RequestException handling remains the same) ...
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
        full_error_msg = f"调用 AI 服务 {config_name_for_error} 出错。状态码: {status_code}. 详情: {error_detail}"
        if response_text: full_error_msg += f". 原始响应: {response_text[:500]}"
        print(full_error_msg)
        user_facing_error = f"调用 AI 服务时出错: {error_detail}"
        if "API key" in error_detail: user_facing_error = "AI 服务认证失败或配置错误"
        if enable_streaming: raise ConnectionError(user_facing_error) from e 
        return {"error": user_facing_error}
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"处理 AI 服务 {config_name_for_error} 响应时发生未知错误: {error_trace}")
        error_msg = f"处理 AI 响应时发生内部错误: {str(e)}"
        if enable_streaming: raise RuntimeError(error_msg) from e
        return {"error": error_msg} 