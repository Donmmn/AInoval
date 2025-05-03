from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user # 导入 login_required 和 current_user
# 确保从 .models 包导入，依赖 __init__.py
from .models import FileSystemItem, User, Group, PromptTemplate, AIService # 导入 User 模型和 Group 模型
from . import db # db 通常从 app 包导入
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from datetime import datetime
from .ai_service import call_ai_service
from .utils import process_prompt_template # 导入处理函数

api_bp = Blueprint('api', __name__, url_prefix='/api')

# --- 获取项目列表 ---
@api_bp.route('/items', methods=['GET'])
def get_items():
    parent_id = request.args.get('parent_id')
    # 查询数据库
    query = FileSystemItem.query
    if parent_id == 'root' or parent_id is None:
        query = query.filter(FileSystemItem.parent_id.is_(None))
    else:
        try:
            query = query.filter(FileSystemItem.parent_id == int(parent_id))
        except ValueError:
            return jsonify({'error': 'Invalid parent_id'}), 400

    items = query.order_by(FileSystemItem.order).all()
    # 使用 to_dict 转换，但不递归获取 children，避免数据量过大
    return jsonify([item.to_dict(include_children=False) for item in items])

# --- 创建新项目 ---
@api_bp.route('/items', methods=['POST'])
def create_item():
    data = request.get_json()
    if not data or 'name' not in data or 'type' not in data:
        return jsonify({'error': 'Missing name or type'}), 400

    item_type = data['type']
    if item_type not in ['folder', 'book', 'setting']:
        return jsonify({'error': 'Invalid type'}), 400

    parent_id = data.get('parentId')
    if parent_id == 'root':
        parent_id = None
    elif parent_id is not None:
        try:
            parent_id = int(parent_id)
            # 检查父项是否存在且为文件夹
            parent_item = FileSystemItem.query.get(parent_id)
            if not parent_item or parent_item.item_type != 'folder':
                 return jsonify({'error': 'Invalid parent folder'}), 400
        except ValueError:
             return jsonify({'error': 'Invalid parentId format'}), 400

    # 计算新项目的 order (放在最后)
    max_order = db.session.query(func.max(FileSystemItem.order)).filter(
        FileSystemItem.parent_id == parent_id # 注意 SQLAlchemy 的 == 用法
    ).scalar()
    new_order = (max_order or -1) + 1

    new_item = FileSystemItem(
        name=data['name'],
        item_type=item_type,
        parent_id=parent_id,
        order=new_order,
        settings_data=[] if item_type == 'setting' else None, # 设定书初始化为空列表
        collapsed=True if item_type == 'folder' else None
    )

    db.session.add(new_item)
    db.session.commit()

    return jsonify(new_item.to_dict()), 201 # 返回创建的项和状态码 201

# --- 删除项目 ---
@api_bp.route('/items/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    item = FileSystemItem.query.get_or_404(item_id)

    # 由于模型中设置了 cascade='all, delete-orphan'，SQLAlchemy 会自动处理子项删除
    db.session.delete(item)
    db.session.commit()

    # 删除后需要重新调整同级项目的 order？暂时不处理，保持简单

    return jsonify({'message': f'Item {item_id} deleted'}), 200

# --- 重命名项目 ---
@api_bp.route('/items/<int:item_id>/rename', methods=['PUT'])
def rename_item(item_id):
    item = FileSystemItem.query.get_or_404(item_id)
    data = request.get_json()
    if not data or 'name' not in data or not data['name'].strip():
        return jsonify({'error': 'New name is required'}), 400

    item.name = data['name'].strip()
    db.session.commit()

    return jsonify(item.to_dict()), 200

# --- 移动项目 (处理排序和父级变更) ---
@api_bp.route('/items/<int:item_id>/move', methods=['PUT'])
def move_item(item_id):
    item_to_move = FileSystemItem.query.get_or_404(item_id)
    data = request.get_json()

    target_parent_id = data.get('targetParentId') # 可能为 null (根目录) 或 数字
    target_before_id = data.get('targetBeforeId') # 可选，插入到此 ID 之前

    # --- 参数验证和转换 ---
    new_parent_id = None
    if target_parent_id is not None and target_parent_id != 'root':
        try:
            new_parent_id = int(target_parent_id)
            # 检查目标父级是否存在且为文件夹
            target_parent = FileSystemItem.query.get(new_parent_id)
            if not target_parent or target_parent.item_type != 'folder':
                return jsonify({'error': 'Invalid target parent folder'}), 400
            # 检查是否移动到自身或子文件夹下
            temp_parent = target_parent
            while temp_parent:
                if temp_parent.id == item_to_move.id:
                     return jsonify({'error': 'Cannot move folder into itself or its descendants'}), 400
                temp_parent = temp_parent.parent

        except ValueError:
            return jsonify({'error': 'Invalid targetParentId format'}), 400
    elif target_parent_id == 'root':
         new_parent_id = None

    # --- 核心逻辑：重新计算 order --- 
    # 1. 获取目标父级下的所有兄弟节点 (包括自身，如果父级没变)
    siblings_query = FileSystemItem.query.filter(FileSystemItem.parent_id == new_parent_id)
    # 2. 从旧位置移除 (如果父级改变) 或者从兄弟列表中排除自身 (如果父级未变)
    if item_to_move.parent_id != new_parent_id:
         siblings = siblings_query.order_by(FileSystemItem.order).all()
    else: # 父级未变，排除自己后再排序
         siblings = siblings_query.filter(FileSystemItem.id != item_id).order_by(FileSystemItem.order).all()

    # 3. 确定插入位置
    insert_index = len(siblings) # 默认插入到最后
    if target_before_id is not None:
         try:
             target_before_id_int = int(target_before_id)
             for i, sibling in enumerate(siblings):
                  if sibling.id == target_before_id_int:
                       insert_index = i
                       break
         except ValueError:
             return jsonify({'error': 'Invalid targetBeforeId format'}), 400

    # 4. 更新被移动项的 parent_id
    item_to_move.parent_id = new_parent_id

    # 5. 重新计算所有受影响项的 order
    current_order = 0
    items_to_update = []

    # 添加插入点之前的兄弟节点
    for i in range(insert_index):
        if siblings[i].order != current_order:
             siblings[i].order = current_order
             items_to_update.append(siblings[i])
        current_order += 1

    # 添加被移动的节点
    if item_to_move.order != current_order:
        item_to_move.order = current_order
        items_to_update.append(item_to_move)
    current_order += 1

    # 添加插入点之后的兄弟节点
    for i in range(insert_index, len(siblings)):
         if siblings[i].order != current_order:
             siblings[i].order = current_order
             items_to_update.append(siblings[i])
         current_order += 1

    # 批量提交更改 (如果 SQLAlchemy 版本支持 add_all)
    # db.session.add_all(items_to_update)
    # 否则，单独添加或依赖 SQLAlchemy 的自动脏检查
    db.session.add(item_to_move) # 确保被移动的项被添加
    for item in items_to_update:
         if item.id != item_to_move.id: # 防止重复添加
             db.session.add(item)

    db.session.commit()

    return jsonify(item_to_move.to_dict()), 200

# --- 新增：关联设定书 --- 
@api_bp.route('/items/<int:book_id>/associate_setting', methods=['POST'])
def associate_setting(book_id):
    book = FileSystemItem.query.get_or_404(book_id)
    if book.item_type != 'book':
        return jsonify({'error': 'Target item is not a book'}), 400
    
    data = request.get_json()
    if not data or 'settingBookId' not in data:
        return jsonify({'error': 'Missing settingBookId'}), 400
        
    setting_book_id = data['settingBookId']
    
    # 验证 settingBookId 是否有效且类型为 'setting'
    if setting_book_id is not None:
        try:
            setting_book_id = int(setting_book_id)
            setting_book = FileSystemItem.query.get(setting_book_id)
            if not setting_book or setting_book.item_type != 'setting':
                 return jsonify({'error': 'Invalid setting book'}), 400
        except (ValueError, TypeError): # 处理非整数或 None 的情况
             return jsonify({'error': 'Invalid settingBookId format'}), 400
    else:
        # 如果传入 null，表示解除关联
        pass 

    book.setting_book_id = setting_book_id
    db.session.commit()
    
    # 返回更新后的 book 信息，可能包含新的关联 ID
    return jsonify(book.to_dict(include_setting_details=True)), 200 # 返回时包含详情

# --- 获取内容 (书籍/设定) ---
@api_bp.route('/items/<int:item_id>/content', methods=['GET'])
def get_content(item_id):
    item = FileSystemItem.query.get_or_404(item_id)
    if item.item_type == 'book':
        # 修改：同时返回关联的设定书信息（如果存在）
        response_data = {
            'id': item.id,
            'type': 'book',
            'name': item.name,
            'content': item.content or ''
        }
        if item.associated_setting:
            # 确保只返回关联设定书的基本信息，避免循环引用或过多数据
            response_data['associatedSetting'] = {
                'id': item.associated_setting.id,
                'name': item.associated_setting.name,
                'type': 'setting'
                # 不在此处返回 settings_data
            }
            # response_data['associatedSetting'] = item.associated_setting.to_dict() # 旧方式可能数据过多
        return jsonify(response_data)
    elif item.item_type == 'setting':
        # --- 新增逻辑：查找关联此设定的书籍 ---
        associated_book = FileSystemItem.query.filter_by(item_type='book', setting_book_id=item_id).first()
        response_data = item.to_dict() # 获取设定书本身的信息
        if associated_book:
            # 如果找到了关联的书籍，添加基本信息到响应中
            response_data['associatedBookInfo'] = {
                'id': associated_book.id,
                'name': associated_book.name,
                'type': 'book'
            }
        return jsonify(response_data)
        # return jsonify(item.to_dict()) # 旧方式
        # return jsonify({'id': item.id, 'type': 'setting', 'name': item.name, 'settings': item.settings_data or []})
    else:
        return jsonify({'error': 'Item is not a book or setting'}), 400

# --- 更新内容 (书籍/设定) ---
@api_bp.route('/items/<int:item_id>/content', methods=['PUT'])
def update_content(item_id):
    item = FileSystemItem.query.get_or_404(item_id)
    data = request.get_json()
    if not data:
         return jsonify({'error': 'No data provided'}), 400

    if item.item_type == 'book':
        item.content = data.get('content', '')
    elif item.item_type == 'setting':
         # 期望 settings 是一个对象数组，例如 [{text: '...'}, {text: '...'}]
         new_settings = data.get('settings') 
         # Basic validation: check if it's a list
         if isinstance(new_settings, list):
             # Optional: Add more validation here to ensure items in the list are objects with 'text'
             item.settings_data = new_settings
         else:
             return jsonify({'error': 'Invalid settings format, expected a list of objects'}), 400
    else:
         return jsonify({'error': 'Cannot update content for this item type'}), 400

    db.session.commit()
    return jsonify({'message': f'Content for item {item_id} updated'}), 200

# --- （可选）更新文件夹折叠状态 ---
@api_bp.route('/items/<int:item_id>/toggle', methods=['PUT'])
def toggle_folder(item_id):
    item = FileSystemItem.query.get_or_404(item_id)
    if item.item_type != 'folder':
         return jsonify({'error': 'Not a folder'}), 400
    
    data = request.get_json()
    if 'collapsed' not in data or not isinstance(data['collapsed'], bool):
        return jsonify({'error': 'Invalid or missing collapsed status'}), 400

    item.collapsed = data['collapsed']
    db.session.commit()
    return jsonify({'message': f'Folder {item_id} toggled'}), 200 

# --- 新增：更新用户设置 --- 
@api_bp.route('/user/settings', methods=['PUT'])
@login_required
def update_user_settings():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing data'}), 400

    updated = False
    # 目前只处理 auto_save_on_navigate
    if 'auto_save_on_navigate' in data:
        setting_value = data['auto_save_on_navigate']
        if isinstance(setting_value, bool):
            current_user.auto_save_on_navigate = setting_value
            updated = True
        else:
            return jsonify({'error': 'Invalid value for auto_save_on_navigate, boolean expected'}), 400
    
    # 可以扩展以处理其他设置
    # if 'other_setting' in data:
    #    ...处理其他设置...
    #    updated = True

    if updated:
        try:
            db.session.commit()
            return jsonify({'success': True, 'message': 'Settings updated'}), 200
        except Exception as e:
            db.session.rollback()
            print(f"Error updating user settings for user {current_user.id}: {e}")
            return jsonify({'error': 'Failed to save settings'}), 500
    else:
        return jsonify({'message': 'No settings were updated'}), 200 # 或者 304 Not Modified?

# --- 新增：管理员发放点数 ---
@api_bp.route('/user/<int:user_id>/grant_points', methods=['POST'])
@login_required
def grant_points_to_user(user_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin privilege required'}), 403
    
    data = request.get_json()
    if not data or 'points' not in data:
        return jsonify({'error': 'Missing points amount'}), 400
    
    try:
        points_to_add = int(data['points'])
        if points_to_add <= 0:
             return jsonify({'error': 'Points must be positive'}), 400
    except ValueError:
        return jsonify({'error': 'Invalid points amount'}), 400

    user_to_update = User.query.get(user_id)
    if not user_to_update:
        return jsonify({'error': 'User not found'}), 404

    try:
        user_to_update.points = (user_to_update.points or 0) + points_to_add
        db.session.commit()
        return jsonify({
            'success': True, 
            'message': f'Successfully granted {points_to_add} points to {user_to_update.username}',
            'new_balance': user_to_update.points
        }), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error granting points to user {user_id}: {e}")
        return jsonify({'error': 'Failed to grant points'}), 500

# --- 新增：获取未关联设定书的书籍列表 ---
@api_bp.route('/books/unassociated', methods=['GET'])
@login_required
def get_unassociated_books():
    try:
        unassociated_books = FileSystemItem.query.filter(
            FileSystemItem.item_type == 'book',
            FileSystemItem.setting_book_id.is_(None) # 查找 setting_book_id 为 NULL 的书籍
        ).order_by(FileSystemItem.name).all()
        
        # 只返回必要的 ID 和 Name
        result = [{'id': book.id, 'name': book.name} for book in unassociated_books]
        return jsonify(result)
    except Exception as e:
        print(f"Error fetching unassociated books: {e}")
        return jsonify({'error': 'Failed to fetch unassociated books'}), 500

# --- 用户组管理 API ---

# 创建新用户组
@api_bp.route('/groups', methods=['POST'])
@login_required
def create_group():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin privilege required'}), 403
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'error': 'Group name is required'}), 400
    
    group_name = data['name'].strip()
    if not group_name:
        return jsonify({'error': 'Group name cannot be empty'}), 400
        
    existing_group = Group.query.filter_by(name=group_name).first()
    if existing_group:
        return jsonify({'error': 'Group name already exists'}), 409 # 409 Conflict
        
    try:
        new_group = Group(name=group_name)
        db.session.add(new_group)
        db.session.commit()
        # 返回新创建的组的信息，包括 ID
        return jsonify({'id': new_group.id, 'name': new_group.name}), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error creating group: {e}")
        return jsonify({'error': 'Failed to create group'}), 500

# 删除用户组
@api_bp.route('/groups/<int:group_id>', methods=['DELETE'])
@login_required
def delete_group(group_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin privilege required'}), 403
        
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
        
    try:
        # 删除组之前，SQLAlchemy 会自动处理关联表中的记录
        # 如果需要，可以先检查组内是否还有用户并给出警告
        user_count = group.users.count() # 使用 lazy='dynamic' 返回的查询对象
        if user_count > 0:
             # 可以选择强制删除，或返回错误要求先移除用户
             # flash(f'警告：删除的用户组 {group.name} 中仍有 {user_count} 个用户。') # Flash 在 API 中效果不好
             print(f'Warning: Deleting group {group.name} ({group_id}) which contains {user_count} users.')
             # return jsonify({'error': f'Group contains {user_count} users. Please remove them first.'}), 400

        db.session.delete(group)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Group "{group.name}" deleted'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting group {group_id}: {e}")
        return jsonify({'error': 'Failed to delete group'}), 500

# 向用户组添加用户 (通过用户名)
@api_bp.route('/groups/<int:group_id>/users', methods=['POST'])
@login_required
def add_user_to_group(group_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin privilege required'}), 403
        
    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
        
    data = request.get_json()
    if not data or not data.get('username'):
        return jsonify({'error': 'Username is required'}), 400
        
    username = data['username'].strip()
    user_to_add = User.query.filter_by(username=username).first()
    if not user_to_add:
        return jsonify({'error': f'User "{username}" not found'}), 404
        
    # 检查用户是否已在组内
    if user_to_add in group.users:
         return jsonify({'message': f'User "{username}" is already in group "{group.name}"'}), 200 # 或 409?
         
    try:
        group.users.append(user_to_add) # 直接操作 relationship
        db.session.commit()
        return jsonify({
            'success': True, 
            'message': f'User "{username}" added to group "{group.name}"'
            # 可以选择返回用户信息
            # 'user': {'id': user_to_add.id, 'username': user_to_add.username}
            }), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error adding user {username} to group {group_id}: {e}")
        return jsonify({'error': 'Failed to add user to group'}), 500

# 从用户组移除用户
@api_bp.route('/groups/<int:group_id>/users/<int:user_id>', methods=['DELETE'])
@login_required
def remove_user_from_group(group_id, user_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin privilege required'}), 403

    group = Group.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
        
    user_to_remove = group.users.filter(User.id == user_id).first() # 使用 dynamic relationship 查询
    if not user_to_remove:
        return jsonify({'error': 'User not found in this group'}), 404
        
    try:
        group.users.remove(user_to_remove)
        db.session.commit()
        return jsonify({'success': True, 'message': f'User "{user_to_remove.username}" removed from group "{group.name}"'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error removing user {user_id} from group {group_id}: {e}")
        return jsonify({'error': 'Failed to remove user from group'}), 500 

# --- 提示词模板管理 API (修改) ---

@api_bp.route('/prompt-templates', methods=['POST'])
@login_required # 所有登录用户都可以创建模板
def create_prompt_template():
    """创建新的提示词模板 (管理员创建系统模板，普通用户创建个人模板)"""
    data = request.json
    if not data or 'name' not in data or 'template_string' not in data:
        return jsonify({'error': 'Missing name or template_string'}), 400
    
    name = data['name'].strip()
    if not name:
        return jsonify({'error': 'Name cannot be empty'}), 400

    # --- 检查名称唯一性 ---
    owner_id = None # 默认为系统模板
    if not current_user.is_admin:
        owner_id = current_user.id
        # 检查当前用户是否已有同名模板
        existing = PromptTemplate.query.filter_by(user_id=owner_id, name=name).first()
        if existing:
             return jsonify({'error': f'你已经有一个名为 "{name}" 的模板了。'}), 409
    else:
        # 管理员创建系统模板 (user_id is None)
        # 需要检查系统模板名称是否唯一 (如果数据库没做约束)
        existing_system = PromptTemplate.query.filter(PromptTemplate.user_id.is_(None), PromptTemplate.name == name).first()
        if existing_system:
            return jsonify({'error': f'已存在名为 "{name}" 的系统模板。'}), 409

    # --- 创建模板 ---
    is_default = data.get('is_default', False) if current_user.is_admin else False # 只有管理员能设置默认

    new_template = PromptTemplate(
        name=name,
        template_string=data['template_string'],
        is_default=bool(is_default),
        user_id=owner_id # 设置所有者 ID
    )
    db.session.add(new_template)
    db.session.commit()
    return jsonify(new_template.to_dict()), 201


@api_bp.route('/prompt-templates', methods=['GET'])
@login_required # 所有登录用户都可以获取
def get_prompt_templates():
    """获取对当前用户可见的提示词模板列表 (系统模板 + 用户自己的模板)"""
    templates = PromptTemplate.query.filter(
        (PromptTemplate.user_id.is_(None)) | (PromptTemplate.user_id == current_user.id)
    ).order_by(PromptTemplate.user_id.isnot(None), PromptTemplate.name).all() # 系统模板优先，然后按名称排序
    return jsonify([t.to_dict() for t in templates])

@api_bp.route('/prompt-templates/<int:template_id>', methods=['GET'])
@login_required
def get_prompt_template(template_id):
    """获取单个提示词模板详情 (用户只能获取系统或自己的)"""
    template = PromptTemplate.query.get_or_404(template_id)
    # --- 权限检查 --- 
    is_system_template = template.user_id is None
    is_owner = template.user_id == current_user.id

    if not is_system_template and not is_owner and not current_user.is_admin: # 管理员可以查看所有？(暂时不允许)
        # 如果不是系统模板，也不是用户自己的，则无权访问
        return jsonify({'error': '无权访问此模板'}), 403

    return jsonify(template.to_dict())

@api_bp.route('/prompt-templates/<int:template_id>', methods=['PUT'])
@login_required
def update_prompt_template(template_id):
    """更新指定的提示词模板 (管理员只能改系统，用户只能改自己的)"""
    template = PromptTemplate.query.get_or_404(template_id)
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # --- 权限检查 --- 
    can_edit = False
    if current_user.is_admin and template.user_id is None: # 管理员编辑系统模板
        can_edit = True
    elif template.user_id == current_user.id: # 用户编辑自己的模板 (无论是否是 admin)
        can_edit = True

    if not can_edit:
        return jsonify({'error': '无权修改此模板'}), 403

    # --- 名称唯一性检查 --- 
    if 'name' in data:
        new_name = data['name'].strip()
        if not new_name:
            return jsonify({'error': '模板名称不能为空'}), 400

        owner_id_to_check = template.user_id # 检查同属主的模板
        # 需要区分检查系统模板还是用户模板
        if owner_id_to_check is None: 
             # 检查系统模板重名
             existing_system = PromptTemplate.query.filter(
                 PromptTemplate.user_id.is_(None),
                 PromptTemplate.name == new_name,
                 PromptTemplate.id != template_id
            ).first()
             if existing_system:
                 return jsonify({'error': f'已存在名为 "{new_name}" 的系统模板。'}), 409
        else:
             # 检查用户模板重名
             existing_user = PromptTemplate.query.filter(
                 PromptTemplate.user_id == owner_id_to_check,
                 PromptTemplate.name == new_name,
                 PromptTemplate.id != template_id
            ).first()
             if existing_user:
                 return jsonify({'error': f'你已经有一个名为 "{new_name}" 的模板了。'}), 409
        
        template.name = new_name
    # --- 结束名称检查 --- 

    if 'template_string' in data:
        template.template_string = data['template_string']

    # 只有管理员能修改系统模板的 is_default 状态
    if 'is_default' in data and current_user.is_admin and template.user_id is None:
         template.is_default = bool(data['is_default'])

    template.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(template.to_dict())


@api_bp.route('/prompt-templates/<int:template_id>', methods=['DELETE'])
@login_required
def delete_prompt_template(template_id):
    """删除指定的提示词模板 (管理员只能删系统，用户只能删自己的)"""
    template = PromptTemplate.query.get_or_404(template_id)

    # --- 权限检查 --- 
    can_delete = False
    if current_user.is_admin and template.user_id is None: # 管理员删除系统模板
        can_delete = True
    elif template.user_id == current_user.id: # 用户删除自己的模板
        can_delete = True

    if not can_delete:
        return jsonify({'error': '无权删除此模板'}), 403

    db.session.delete(template)
    db.session.commit()
    return '', 204

# --- 新增：管理员获取所有用户模板 API --- 
@api_bp.route('/admin/user-prompt-templates', methods=['GET'])
@login_required
def admin_get_user_prompt_templates():
    """(仅管理员) 获取所有用户的提示词模板列表"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    # 查询所有 user_id 不为空的模板，并加载关联的 owner 用户信息
    user_templates = PromptTemplate.query.options(
        db.joinedload(PromptTemplate.owner) # 预加载用户信息避免 N+1 查询
    ).filter(
        PromptTemplate.user_id.isnot(None)
    ).order_by(User.username, PromptTemplate.name).all() # 按用户名，再按模板名排序

    return jsonify([t.to_dict() for t in user_templates])

# --- 使用模板生成提示词 API ---
@api_bp.route('/generate-with-template', methods=['POST'])
@login_required # 假设需要用户登录才能生成
def generate_prompt_with_template():
    """根据用户选择的模板和输入数据，调用AI生成内容"""
    data = request.json
    template_id = data.get('template_id')
    user_input_data = data.get('input_data') # 包含 前文, 后文, 提示词, 字数, 风格, 设定
    ai_service_config_id = data.get('ai_service_config_id') # Get the selected AI service config ID

    if not template_id or not user_input_data or not ai_service_config_id: # Added check for ai_service_config_id
        missing_fields = []
        if not template_id: missing_fields.append('template_id')
        if not user_input_data: missing_fields.append('input_data')
        if not ai_service_config_id: missing_fields.append('ai_service_config_id')
        return jsonify({'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400

    # 验证 template_id 和 ai_service_config_id 是否为有效整数
    try:
        template_id = int(template_id)
        ai_service_config_id = int(ai_service_config_id) # Added validation for config ID
    except ValueError:
        return jsonify({'error': 'Invalid template_id or ai_service_config_id format (must be integer)'}), 400

    template = PromptTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404
        
    # --- 权限检查 (模板): 用户只能使用系统或自己的模板 ---
    is_system_template = template.user_id is None
    is_owner = template.user_id == current_user.id
    if not is_system_template and not is_owner:
         # 暂时不允许管理员使用其他用户的模板 (除非未来有特殊需求)
         return jsonify({'error': '无权使用此模板'}), 403

    # --- 处理设定数据 (与之前相同) ---
    enabled_settings_text = []
    raw_settings = user_input_data.get('设定') 
    
    if isinstance(raw_settings, list):
        for setting_item in raw_settings:
            if isinstance(setting_item, dict) and \
               setting_item.get('enabled') is True and \
               'text' in setting_item and setting_item['text']: 
                 enabled_settings_text.append(f"- {str(setting_item['text']).strip()}")
    elif isinstance(raw_settings, str) and raw_settings.strip():
        enabled_settings_text.append(raw_settings.strip())
        
    processed_settings = "\n".join(enabled_settings_text)
    
    processed_input_data = user_input_data.copy() 
    processed_input_data['设定'] = processed_settings

    # --- 调用工具函数生成最终提示词 (与之前相同) ---
    try:
        final_prompt = process_prompt_template(template.template_string, processed_input_data)
    except KeyError as e:
        # 更具体的错误捕获：模板中使用了 input_data 未提供的变量
        print(f"Error processing template {template_id}: Missing variable {e}")
        return jsonify({'error': f"模板渲染错误：缺少变量 '{e}'"}), 400
    except Exception as e:
        print(f"Error processing prompt template {template_id}: {e}") # 记录日志
        return jsonify({'error': '处理提示词模板时出错'}), 500

    # --- 调用 AI 服务 ---
    print(f"User {current_user.id} calling AI service {ai_service_config_id} with template {template_id}") # Log
    ai_result = call_ai_service(prompt=final_prompt, config_id=ai_service_config_id)

    # --- 处理 AI 服务返回结果 ---
    if "error" in ai_result:
        # 将 AI 服务返回的错误传递给前端
        # 可以考虑根据错误类型设置不同的状态码，但 500 (Internal Server Error / Service Error) 通常是安全的
        print(f"AI service call failed for user {current_user.id}: {ai_result['error']}") # Log error
        # 返回给前端的错误信息可以稍微通用一些，避免泄露过多内部细节
        user_error_message = ai_result['error'] 
        # 可以根据需要屏蔽或修改特定的错误信息
        # 例如，如果错误包含 API Key，需要过滤掉
        if "API key" in user_error_message:
             user_error_message = "AI 服务认证失败或配置错误" # Generic message
             
        return jsonify({'error': f"调用 AI 服务失败: {user_error_message}"}), 502 # 502 Bad Gateway is often suitable for upstream errors
    
    elif "success" in ai_result and "content" in ai_result:
        # 成功获取 AI 生成的内容
        generated_text = ai_result['content']
        print(f"AI service call successful for user {current_user.id}. Response length: {len(generated_text)}") # Log success
        # TODO: 在这里可以添加积分扣除等逻辑
        # deduct_user_points(current_user, cost_of_generation)

        return jsonify({
            'generated_text': generated_text,
            # Optionally return the prompt used for debugging/reference
            # 'final_prompt_used': final_prompt 
        }), 200
    else:
        # 如果 AI 服务返回了未预期的格式 (非 error 也非 success)
        print(f"Unexpected response format from call_ai_service for user {current_user.id}: {ai_result}") # Log
        return jsonify({'error': 'AI 服务返回了无效的响应格式'}), 500

# --- 新增：将用户 AI 服务配置设为系统服务 --- 
@api_bp.route('/ai-services/<int:config_id>/make-system', methods=['PUT'])
@login_required
def make_ai_service_system(config_id):
    """ (仅管理员) 将指定的自定义 AI 服务配置标记为系统预设服务。"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    service_config = AIService.query.get(config_id)

    if not service_config:
        return jsonify({'error': 'AI 服务配置未找到'}), 404
        
    # 确保操作的是用户自定义的服务 (虽然理论上管理员可以改任何东西，但此操作目标是提升用户配置)
    if service_config.is_system_service:
         return jsonify({'message': '此配置已经是系统预设服务'}), 200 # 或者返回 400 Bad Request?

    try:
        original_owner_id = service_config.owner_id # 可选：记录原始所有者？
        service_config.is_system_service = True
        # service_config.owner_id = None # Keep the original owner ID (admin)
        db.session.commit()
        print(f"Admin {current_user.id} marked AI Service {config_id} (owned by {original_owner_id}) as system service.") # Updated log message
        return jsonify({'success': True, 'message': f'配置 "{service_config.name}" 已成功设为系统预设服务。'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error making AI Service {config_id} system: {e}")
        return jsonify({'error': '更新配置时出错'}), 500

# --- 新增：删除 AI 服务配置 (管理员权限) ---
@api_bp.route('/ai-services/<int:config_id>', methods=['DELETE'])
@login_required
def delete_ai_service(config_id):
    """ (仅管理员) 删除指定的 AI 服务配置 (包括系统预设和用户自定义的)。"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    service_config = AIService.query.get(config_id)

    if not service_config:
        return jsonify({'error': 'AI 服务配置未找到'}), 404

    try:
        config_name = service_config.name
        config_type = "系统预设" if service_config.is_system_service else "用户自定义"
        db.session.delete(service_config)
        db.session.commit()
        print(f"Admin {current_user.id} deleted AI Service {config_id} ('{config_name}', type: {config_type}).")
        return jsonify({'success': True, 'message': f'{config_type}配置 "{config_name}" 已成功删除。'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting AI Service {config_id}: {e}")
        return jsonify({'error': '删除配置时出错'}), 500

# --- 新增：更新 AI 服务配置 --- 
@api_bp.route('/ai-services/<int:config_id>', methods=['PUT'])
@login_required
def update_ai_service(config_id):
    """ 更新指定的 AI 服务配置。管理员可修改系统配置，用户只能修改自己的配置。 """
    service_config = AIService.query.get(config_id)
    if not service_config:
        return jsonify({'error': 'AI 服务配置未找到'}), 404

    data = request.json
    if not data:
        return jsonify({'error': '缺少更新数据'}), 400

    # --- 权限检查 --- 
    can_edit = False
    if service_config.is_system_service:
        # 系统配置：只有管理员可以编辑
        if current_user.is_admin:
            can_edit = True
    else:
        # 用户配置：只有所有者可以编辑
        if service_config.owner_id == current_user.id:
            can_edit = True

    if not can_edit:
        return jsonify({'error': '无权修改此配置'}), 403

    # --- 更新字段 --- 
    updated_fields = []
    if 'name' in data and data['name'].strip():
        service_config.name = data['name'].strip()
        updated_fields.append('name')
    if 'service_type' in data and data['service_type']:
        # TODO: Add validation for allowed service_types?
        service_config.service_type = data['service_type']
        updated_fields.append('service_type')
    if 'base_url' in data and data['base_url']:
        service_config.base_url = data['base_url']
        updated_fields.append('base_url')
    if 'model_name' in data and data['model_name']:
        service_config.model_name = data['model_name']
        updated_fields.append('model_name')
    # API Key: Only update if a non-empty value is provided. Allows clearing if needed?
    # If data contains 'api_key' but its value is empty string, should we clear the key?
    # Let's update only if the key is present and non-null. Frontend should handle empty submission.
    if 'api_key' in data and data['api_key'] is not None: # Allow empty string to potentially clear key
         # Consider encrypting API key before storing
         service_config.api_key = data['api_key'] 
         updated_fields.append('api_key')

    if not updated_fields:
        return jsonify({'message': '未提供有效更新字段'}), 400 # Or maybe 304 Not Modified?

    try:
        db.session.commit()
        print(f"User {current_user.id} updated AI Service {config_id}. Fields: {updated_fields}")
        # Return the updated config (excluding API key for safety)
        updated_data = service_config.to_dict() # Assuming to_dict method exists and excludes sensitive data
        if 'api_key' in updated_data: del updated_data['api_key'] # Ensure key isn't returned
        return jsonify(updated_data), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error updating AI Service {config_id}: {e}")
        return jsonify({'error': '更新配置时出错'}), 500

# --- 新增：更新用户启用的 AI 服务配置 ---
@api_bp.route('/user/settings/active-ai-service', methods=['PUT'])
@login_required
def set_active_ai_service():
    """ 更新当前用户启用的 AI 服务配置 ID。 """
    data = request.json
    if not data or 'config_id' not in data:
        return jsonify({'error': '缺少 config_id'}), 400

    config_id = data.get('config_id')

    # 允许用户设置为 None (不选择任何配置)
    if config_id is None:
        current_user.active_ai_service_id = None
        try:
            db.session.commit()
            return jsonify({'success': True, 'message': '已取消启用 AI 服务配置。'}), 200
        except Exception as e:
            db.session.rollback()
            print(f"Error setting active AI service to None for user {current_user.id}: {e}")
            return jsonify({'error': '更新设置时出错'}), 500

    # 验证 config_id 是否为有效整数
    try:
        config_id = int(config_id)
    except (ValueError, TypeError):
        return jsonify({'error': '无效的 config_id 格式'}), 400

    # 验证配置是否存在且用户有权使用
    service_config = AIService.query.get(config_id)
    if not service_config:
        return jsonify({'error': '指定的 AI 服务配置未找到'}), 404

    # 检查权限：必须是系统服务，或者是用户自己的服务
    is_accessible = service_config.is_system_service or (service_config.owner_id == current_user.id)
    if not is_accessible:
        return jsonify({'error': '无权启用此 AI 服务配置'}), 403

    # 更新用户的 active_ai_service_id
    current_user.active_ai_service_id = config_id
    try:
        db.session.commit()
        print(f"User {current_user.id} set active AI service to {config_id} ('{service_config.name}')")
        return jsonify({'success': True, 'message': f'已启用 AI 服务配置: {service_config.name}'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error setting active AI service to {config_id} for user {current_user.id}: {e}")
        return jsonify({'error': '更新设置时出错'}), 500

# --- 新增：获取当前用户可用的 AI 服务配置列表 --- 
@api_bp.route('/ai-services/available', methods=['GET'])
@login_required
def get_available_ai_services():
    """ 获取当前用户可以使用的 AI 服务配置列表 (系统服务 + 用户自己的服务)。"""
    # 查询系统服务
    system_services = AIService.query.filter_by(is_system_service=True).all()
    # 查询用户自己的服务
    user_services = AIService.query.filter_by(owner_id=current_user.id, is_system_service=False).all()
    
    # 合并列表并转换为字典格式 (仅包含必要信息 id 和 name)
    available_configs = []
    for config in system_services + user_services:
        # 添加标记区分系统服务和用户服务 (可选)
        config_type = "(系统)" if config.is_system_service else "(我的)"
        available_configs.append({
            'id': config.id,
            'name': f"{config.name} {config_type}" # 在名称后添加类型标记
        })
        
    # 可以根据需要添加排序逻辑，例如将用户自己的排在前面
    # sorted_configs = sorted(available_configs, key=lambda x: '我的' not in x['name']) 

    return jsonify(available_configs)
