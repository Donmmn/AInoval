from flask import Blueprint, jsonify, request, Response, current_app
from flask_login import login_required, current_user # 导入 login_required 和 current_user
# 确保从 .models 包导入，依赖 __init__.py
from .models import FileSystemItem, User, Group, PromptTemplate, AIService, ApiCallLog # 导入 ApiCallLog
from . import db # db 通常从 app 包导入
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from datetime import datetime
from .ai_service import call_ai_service
from .utils import process_prompt_template # 导入处理函数
import os # For file path operations
import json # For JSON handling

api_bp = Blueprint('api', __name__, url_prefix='/api')

TOKENS_PER_POINT = 100

# --- 获取项目列表 ---
@api_bp.route('/items', methods=['GET'])
@login_required
def get_items():
    parent_id_str = request.args.get('parent_id')
    
    # 基础查询，限定当前用户
    query = FileSystemItem.query.filter_by(user_id=current_user.id)

    if parent_id_str == 'root' or parent_id_str is None:
        query = query.filter(FileSystemItem.parent_id.is_(None))
    else:
        try:
            parent_id = int(parent_id_str)
            # 确保父文件夹也属于当前用户
            parent_item = FileSystemItem.query.filter_by(id=parent_id, user_id=current_user.id).first()
            if not parent_item:
                # 如果父文件夹不属于当前用户或不存在，则不返回任何子项
                return jsonify([]), 200
            query = query.filter(FileSystemItem.parent_id == parent_id)
        except ValueError:
            return jsonify({'error': 'Invalid parent_id'}), 400

    items = query.order_by(FileSystemItem.order).all()
    return jsonify([item.to_dict(include_children=False) for item in items])

# --- 创建新项目 ---
@api_bp.route('/items', methods=['POST'])
@login_required
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
            parent_item = FileSystemItem.query.filter_by(id=parent_id, user_id=current_user.id).first()
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
        user_id=current_user.id,
        order=new_order,
        settings_data=[] if item_type == 'setting' else None, # 设定书初始化为空列表
        collapsed=True if item_type == 'folder' else None
    )

    db.session.add(new_item)
    db.session.commit()

    return jsonify(new_item.to_dict()), 201 # 返回创建的项和状态码 201

# --- 删除项目 ---
@api_bp.route('/items/<int:item_id>', methods=['DELETE'])
@login_required
def delete_item(item_id):
    item = FileSystemItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404(
        description=f'Item {item_id} not found or you do not have permission.'
    )

    # 由于模型中设置了 cascade='all, delete-orphan'，SQLAlchemy 会自动处理子项删除
    db.session.delete(item)
    db.session.commit()

    # 删除后需要重新调整同级项目的 order？暂时不处理，保持简单

    return jsonify({'message': f'Item {item_id} deleted'}), 200

# --- 重命名项目 ---
@api_bp.route('/items/<int:item_id>/rename', methods=['PUT'])
@login_required
def rename_item(item_id):
    item = FileSystemItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404(
        description=f'Item {item_id} not found or you do not have permission.'
    )
    data = request.get_json()
    if not data or 'name' not in data or not data['name'].strip():
        return jsonify({'error': 'New name is required'}), 400

    item.name = data['name'].strip()
    db.session.commit()

    return jsonify(item.to_dict()), 200

# --- 移动项目 (处理排序和父级变更) ---
@api_bp.route('/items/<int:item_id>/move', methods=['PUT'])
@login_required
def move_item(item_id):
    item_to_move = FileSystemItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404(
        description=f'Item {item_id} to move not found or you do not have permission.'
    )
    data = request.get_json()

    target_parent_id = data.get('targetParentId') # 可能为 null (根目录) 或 数字
    target_before_id = data.get('targetBeforeId') # 可选，插入到此 ID 之前

    # --- 参数验证和转换 ---
    new_parent_id = None
    if target_parent_id is not None and target_parent_id != 'root':
        try:
            new_parent_id = int(target_parent_id)
            # 检查目标父级是否存在且为文件夹
            target_parent = FileSystemItem.query.filter_by(id=new_parent_id, user_id=current_user.id, item_type='folder').first()
            if not target_parent:
                return jsonify({'error': 'Target parent folder not found or not accessible'}), 400
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
         siblings = siblings_query.filter(FileSystemItem.id != item_to_move.id).order_by(FileSystemItem.order).all()

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
@login_required
def associate_setting(book_id):
    book = FileSystemItem.query.filter_by(id=book_id, user_id=current_user.id, item_type='book').first_or_404(
        description=f'Book {book_id} not found or you do not have permission.'
    )
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
            setting_book = FileSystemItem.query.filter_by(id=setting_book_id, user_id=current_user.id, item_type='setting').first_or_404(
                description=f'Setting book {setting_book_id} not found or you do not have permission.'
            )
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
@login_required
def get_content(item_id):
    item = FileSystemItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404(
        description=f'Item {item_id} not found or you do not have permission.'
    )
    if item.item_type == 'book':
        # 获取关联的设定书信息（如果存在）
        associated_setting_info = None
        if item.associated_setting: # associated_setting 是关系对象
            associated_setting_info = {
                'id': item.associated_setting.id,
                'name': item.associated_setting.name,
                'type': item.associated_setting.item_type
            }
        return jsonify({
            'content': item.content or '', 
            'associatedSetting': associated_setting_info
        })
    elif item.item_type == 'setting':
        # 检查此设定书是否关联了书籍，如果是，则返回书籍信息
        # 注意：'associated_books' 是 FileSystemItem.associated_setting 的 backref
        # 如果一个设定书可能关联多个书籍（虽然当前模型是一对一），这里需要调整
        associated_book_info = None
        # 假设一个设定书只被一个书关联 (如果 FileSystemItem.associated_setting 的 backref 是 uselist=False)
        # 如果不是， associated_books 会是一个列表
        # 暂时简化：如果有关联，取第一个（或不取，取决于业务逻辑）
        # 这个 backref 可能需要更明确的定义或使用方式
        # 让我们先返回设定书自身的内容
        if item.associated_books: # 这是一个列表
            first_associated_book = item.associated_books[0] # 取第一个为例
            associated_book_info = {
                'id': first_associated_book.id,
                'name': first_associated_book.name,
                'type': first_associated_book.item_type
            }

        return jsonify({
            'id': item.id, 
            'name': item.name, 
            'type': item.item_type, 
            'settings': item.settings_data or [],
            'associatedBookInfo': associated_book_info # 新增字段，用于前端判断
        })
    else:
        return jsonify({'error': 'Item is not a book or setting'}), 400

# --- 更新内容 (书籍/设定) ---
@api_bp.route('/items/<int:item_id>/content', methods=['PUT'])
@login_required
def update_content(item_id):
    item = FileSystemItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404(
        description=f'Item {item_id} not found or you do not have permission for update.'
    )
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
@login_required
def toggle_folder(item_id):
    item = FileSystemItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404(
        description=f'Folder {item_id} not found or you do not have permission.'
    )
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
            FileSystemItem.user_id == current_user.id,
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

# 新增：获取所有用户组列表
@api_bp.route('/groups', methods=['GET'])
@login_required
def get_all_groups():
    if not current_user.is_admin: # 或者根据您的权限需求调整
        return jsonify({'error': '权限不足'}), 403
    groups = Group.query.order_by(Group.name).all()
    return jsonify([{'id': group.id, 'name': group.name} for group in groups])

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
@login_required
def generate_prompt_with_template():
    # --- 提前获取 App 实例和当前用户信息 --- 
    app = current_app._get_current_object()
    user_id = current_user.id
    username = current_user.username
    is_admin_flag = current_user.is_admin
    user_id_for_log = user_id # Use the captured ID for logging

    # 确保用户点数不为 None...
    if current_user.is_authenticated and hasattr(current_user, 'points') and current_user.points is None:
        current_user.points = 0
        print(f"User {user_id_for_log}: Points initialized to 0 as it was None.")

    try:
        # ... (AI Config lookup logic - remains the same, but use captured user_id for owner check if needed) ...
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        template_id = data.get('template_id')
        input_data = data.get('input_data', {})
        requested_ai_config_id = data.get('ai_service_config_id') 

        ai_config = None
        error_message = None
        config_id_to_use = None

        # Determine config_id_to_use (logic remains the same)
        if requested_ai_config_id and str(requested_ai_config_id).isdigit():
            try:
                config_id_to_use = int(requested_ai_config_id)
            except (ValueError, TypeError):
                error_message = "无效的 AI 服务 ID"
        elif requested_ai_config_id == "" or requested_ai_config_id is None:
            # Use current_user here as it's still valid
            if current_user.is_authenticated and current_user.active_ai_service_id:
                config_id_to_use = current_user.active_ai_service_id
            else:
                system_default_config = AIService.query.filter_by(is_default=True, is_system_service=True).first()
                if system_default_config:
                    config_id_to_use = system_default_config.id
                else:
                    error_message = "未配置默认 AI 服务"
        else:
             error_message = "无效的 AI 服务 ID"
        
        # Get ai_config (logic remains the same, uses config_id_to_use)
        if config_id_to_use and not error_message:
            ai_config = AIService.query.get(config_id_to_use)
            if not ai_config:
                error_message = f"找不到 ID 为 {config_id_to_use} 的 AI 服务"
            else: # Check access permission using captured user_id
                 user_owns = ai_config.owner_id == user_id # Use captured user_id
                 is_accessible = ai_config.is_system_service or user_owns
                 if not is_accessible:
                     error_message = f"无权使用 AI 服务 '{ai_config.name}'"

        if error_message:
            print(f"User {user_id_for_log}: AI service config lookup failed: {error_message}")
            return jsonify({'error': error_message}), 400
        if not ai_config: 
             print(f"User {user_id_for_log}: Error - ai_config is None after setup, aborting.")
             return jsonify({'error': '无法确定要使用的 AI 服务配置'}), 500
            
        print(f"User {user_id_for_log}: Determined to use AI Service: ID={ai_config.id}, Name='{ai_config.name}', Streaming={ai_config.enable_streaming}")

        # --- 点数预检查 (使用 current_user.points 和 is_admin_flag) ---
        MINIMUM_POINTS_REQUIRED = 1 
        if not is_admin_flag: # Use the captured flag
            # It's okay to use current_user.points here as context is still valid
            user_points_to_check = current_user.points 
            if user_points_to_check < MINIMUM_POINTS_REQUIRED:
                print(f"User {user_id_for_log}: Insufficient points ({user_points_to_check}) ...")
                return jsonify({
                    'error': f'点数不足 (您有 {user_points_to_check} 点)，请充值后再使用AI服务。',
                    'code': 'INSUFFICIENT_POINTS'
                }), 402
            else:
                print(f"User {user_id_for_log}: Points check passed ({user_points_to_check} points) ...")

        # --- 提示词处理 --- 
        final_prompt = None
        if template_id:
            try:
                template_id_int = int(template_id)
                template = PromptTemplate.query.get(template_id_int)
                if not template:
                    return jsonify({'error': f'Template with id {template_id_int} not found'}), 404
                final_prompt = process_prompt_template(template.template_string, input_data)
            except ValueError:
                return jsonify({'error': 'Invalid template_id format'}), 400
            except Exception as e:
                print(f"User {user_id_for_log}: Error processing template {template_id}: {e}")
                return jsonify({'error': f'Error processing template: {e}'}), 500
        else:
            final_prompt = input_data.get('提示词', '')
            if not final_prompt:
                 return jsonify({'error': 'No template selected and no prompt provided in input_data using key "提示词"'}), 400

        # --- 新增：打印最终提示词到后台日志 ---
        print(f"\n--- User {user_id_for_log} | Final Prompt for AI Service ID {ai_config.id} ---")
        print(final_prompt)
        print("--- End of Final Prompt ---\n")
        # --- 结束打印 ---

        # --- 调用 AI 服务 --- 
        if ai_config.enable_streaming:
            config_details_for_stream = { # ... (remains the same) ...
                 'api_key': ai_config.api_key,
                 'base_url': ai_config.base_url,
                 'model_name': ai_config.model_name,
                 'service_type': ai_config.service_type,
                 'name': ai_config.name 
            }
            
            # 定义 stream_generator，接收 app, user_id, username, is_admin
            def stream_generator(flask_app, gen_user_id, gen_username, gen_is_admin):
                try:
                    print(f"User {gen_user_id}: Starting stream generation with AI service '{ai_config.name}'.")
                    token_info = {'total': 0}
                    stream_iterator = call_ai_service(final_prompt, 
                                                      config_details=config_details_for_stream, 
                                                      enable_streaming=True, 
                                                      token_info=token_info)
                    
                    for chunk in stream_iterator:
                        yield chunk
                    
                    print(f"User {gen_user_id}: Stream generation finished for AI service '{ai_config.name}'.")
                    total_tokens_consumed_stream = token_info.get('total', 0) 
                    print(f"User {gen_user_id}: Tokens consumed: {total_tokens_consumed_stream}. Attempting billing and logging.")
                    
                    with flask_app.app_context():
                        actual_points_deducted = 0 
                        billing_success = False
                        log_success = False
                        try:
                            if total_tokens_consumed_stream > 0:
                                points_to_deduct_calc = total_tokens_consumed_stream // TOKENS_PER_POINT
                                if points_to_deduct_calc > 0:
                                    if not gen_is_admin: # 使用传入的 is_admin 标志
                                        user_for_billing = User.query.get(gen_user_id) # 使用传入的 user_id
                                        if user_for_billing:
                                            if user_for_billing.points is None: user_for_billing.points = 0
                                            old_points = user_for_billing.points
                                            user_for_billing.points -= points_to_deduct_calc
                                            actual_points_deducted = points_to_deduct_calc 
                                            db.session.add(user_for_billing) 
                                            billing_success = True 
                                            print(f"User {gen_user_id} will be billed {points_to_deduct_calc} points. Tokens: {total_tokens_consumed_stream}. Points before: {old_points}, After: {user_for_billing.points}. AI: {ai_config.name}")
                                        else:
                                            print(f"CRITICAL ERROR: User {gen_user_id} not found in DB for billing.")
                                    else:
                                        print(f"User {gen_user_id} (admin) used {total_tokens_consumed_stream} tokens. Billing skipped.")
                                else:
                                    print(f"User {gen_user_id}: Points to deduct is 0 for {total_tokens_consumed_stream} tokens. No billing action.")
                            else:
                                print(f"User {gen_user_id}: Token usage was 0 or not found. No billing or logging.")

                            if total_tokens_consumed_stream > 0:
                                new_log_entry = ApiCallLog(
                                    user_id=gen_user_id, # 使用传入的 user_id
                                    username=gen_username, # 使用传入的 username
                                    ai_service_id=ai_config.id,
                                    ai_service_name=ai_config.name,
                                    is_system_service=ai_config.is_system_service,
                                    tokens_consumed=total_tokens_consumed_stream,
                                    points_deducted=actual_points_deducted, 
                                    prompt_length=len(final_prompt) if final_prompt else 0 
                                )
                                db.session.add(new_log_entry)
                                log_success = True 
                                print(f"User {gen_user_id}: API call log entry created. Tokens: {total_tokens_consumed_stream}, Points deducted: {actual_points_deducted}.")
                        
                        except Exception as db_op_ex:
                            print(f"CRITICAL ERROR during billing/logging setup for User {gen_user_id}: {db_op_ex}")
                            db.session.rollback()
                        
                        else: 
                            if billing_success or log_success:
                                try:
                                    db.session.commit()
                                    print(f"User {gen_user_id}: Database session committed for billing/logging.")
                                except Exception as commit_ex:
                                    db.session.rollback()
                                    print(f"CRITICAL ERROR: Failed to commit database session for User {gen_user_id}: {commit_ex}")
                            else:
                                print(f"User {gen_user_id}: No database changes to commit for this request.")
                except Exception as e:
                     print(f"!!! User {gen_user_id}: Error during streaming generation for AI '{config_details_for_stream.get('name', 'N/A')}': {e}") # Log gen_user_id
                     import traceback
                     print(traceback.format_exc())
            
            # 返回 Response 时，调用 stream_generator 并传入 app 和用户信息
            return Response(stream_generator(app, user_id, username, is_admin_flag), mimetype='text/plain') 
        else:
             # ... (非流式逻辑保持不变) ...
            print(f"User {user_id_for_log}: Non-streaming path taken for AI service '{ai_config.name}'. Billing logic for non-streaming is disabled.")
            result = call_ai_service(final_prompt, config_id=ai_config.id, enable_streaming=False)
            if 'error' in result: 
                return jsonify({'error': result['error']}), result.get('status_code', 500)
            else:
                 return jsonify({'generated_text': result.get('content', '')}) 
                
    except Exception as e:
        # ... (外部错误处理保持不变) ...
        import traceback
        print(f"User {user_id_for_log}: Unhandled error in generate_prompt_with_template: {traceback.format_exc()}")
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

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

# --- 新增：管理员获取 API 调用日志 ---
@api_bp.route('/admin/api-call-logs', methods=['GET'])
@login_required
def admin_get_api_call_logs():
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int) 
        
        logs_query = ApiCallLog.query.order_by(ApiCallLog.timestamp.desc())
        
        paginated_logs = logs_query.paginate(page=page, per_page=per_page, error_out=False)
        logs_data = [log.to_dict() for log in paginated_logs.items]
        
        return jsonify({
            'logs': logs_data,
            'total_logs': paginated_logs.total,
            'current_page': paginated_logs.page,
            'per_page': paginated_logs.per_page,
            'total_pages': paginated_logs.pages
        })
    except Exception as e:
        print(f"Error fetching API call logs: {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': '获取 API 调用日志失败'}), 500

# --- 新增：获取当前用户状态（包括点数） ---
@api_bp.route('/user/status', methods=['GET'])
@login_required
def get_user_status():
    # current_user 由 Flask-Login 提供
    user_data = {
        'id': current_user.id,
        'username': current_user.username,
        'points': current_user.points if hasattr(current_user, 'points') else 0, # 确保返回数字
        'is_admin': current_user.is_admin,
        'active_ai_service_id': current_user.active_ai_service_id
        # 可以根据需要添加更多字段
    }
    return jsonify(user_data)

# --- 新增：用户 AI 偏好设置 API --- 
@api_bp.route('/user/ai-preferences', methods=['GET'])
@login_required
def get_user_ai_preferences():
    """获取当前用户的 AI 偏好设置。"""
    # 从 current_user 对象直接获取偏好设置字段
    # 确保 User 模型中定义的默认值在这里能被正确反映（如果用户未设置过）
    prefs = {
        'ai_bg_color_r': current_user.ai_bg_color_r,
        'ai_bg_color_g': current_user.ai_bg_color_g,
        'ai_bg_color_b': current_user.ai_bg_color_b,
        'ai_font_color_r': current_user.ai_font_color_r,
        'ai_font_color_g': current_user.ai_font_color_g,
        'ai_font_color_b': current_user.ai_font_color_b,
        'retry_prompt_template': current_user.retry_prompt_template,
        'enable_markdown_prompt': current_user.enable_markdown_prompt,
        'markdown_prompt_template': current_user.markdown_prompt_template
    }
    # SQLAlchemy 在加载模型实例时，如果数据库中对应列为 NULL，
    # 并且模型定义了 default 值，实例的属性会是该 default 值。
    # 如果数据库中是 NULL 且模型无 default，则属性为 None。
    # 前端在加载时已处理了 null 的情况，所以这里直接返回模型值即可。
    return jsonify(prefs), 200

@api_bp.route('/user/ai-preferences', methods=['PUT'])
@login_required
def update_user_ai_preferences():
    """更新当前用户的 AI 偏好设置。"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing data'}), 400

    try:
        current_user.ai_bg_color_r = data.get('ai_bg_color_r', current_user.ai_bg_color_r)
        current_user.ai_bg_color_g = data.get('ai_bg_color_g', current_user.ai_bg_color_g)
        current_user.ai_bg_color_b = data.get('ai_bg_color_b', current_user.ai_bg_color_b)
        current_user.ai_font_color_r = data.get('ai_font_color_r', current_user.ai_font_color_r)
        current_user.ai_font_color_g = data.get('ai_font_color_g', current_user.ai_font_color_g)
        current_user.ai_font_color_b = data.get('ai_font_color_b', current_user.ai_font_color_b)
        current_user.retry_prompt_template = data.get('retry_prompt_template', current_user.retry_prompt_template)
        current_user.enable_markdown_prompt = data.get('enable_markdown_prompt', current_user.enable_markdown_prompt)
        current_user.markdown_prompt_template = data.get('markdown_prompt_template', current_user.markdown_prompt_template)
        
        # 确保布尔值正确处理
        if 'enable_markdown_prompt' in data and isinstance(data['enable_markdown_prompt'], bool):
            current_user.enable_markdown_prompt = data['enable_markdown_prompt']
        
        # 确保颜色值为整数，在0-255之间，如果提供了的话
        color_fields = [
            'ai_bg_color_r', 'ai_bg_color_g', 'ai_bg_color_b',
            'ai_font_color_r', 'ai_font_color_g', 'ai_font_color_b'
        ]
        for field in color_fields:
            if field in data:
                try:
                    value = int(data[field])
                    if not (0 <= value <= 255):
                        raise ValueError("Color value out of range 0-255")
                    setattr(current_user, field, value)
                except (ValueError, TypeError):
                    # 如果转换失败或类型不对，可以选择忽略、使用默认值或返回错误
                    # 这里选择忽略，保持该字段的原值，前端应该已经做了校验
                    print(f"Warning: Invalid value for {field}: {data[field]}. Keeping original.")
                    pass 

        db.session.commit()
        return jsonify({'message': 'User AI preferences updated successfully.'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error updating user AI preferences for {current_user.username}: {e}")
        return jsonify({'error': str(e)}), 500

# --- 新增：系统默认 AI 偏好设置 API --- 
SYSTEM_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'system_ai_defaults.json')

@api_bp.route('/admin/ai-preferences/system-default', methods=['POST'])
@login_required
def set_system_default_ai_preferences():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing data'}), 400
    
    # Basic validation of expected fields (optional, but good practice)
    expected_keys = [
        'ai_bg_color_r', 'ai_bg_color_g', 'ai_bg_color_b',
        'ai_font_color_r', 'ai_font_color_g', 'ai_font_color_b',
        'retry_prompt_template', 'enable_markdown_prompt', 'markdown_prompt_template'
    ]
    if not all(key in data for key in expected_keys):
        return jsonify({'error': 'Missing some preference keys in data'}), 400

    try:
        config_dir = os.path.dirname(SYSTEM_DEFAULTS_PATH)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
            print(f"Created directory: {config_dir}")

        with open(SYSTEM_DEFAULTS_PATH, 'w') as f:
            json.dump(data, f, indent=4)
        
        return jsonify({'message': 'System default AI preferences saved successfully.'}), 200
    except Exception as e:
        print(f"Error saving system default AI preferences: {e}")
        return jsonify({'error': str(e)}), 500

@api_bp.route('/user/ai-preferences/system-default', methods=['GET'])
@login_required
def get_system_default_ai_preferences():
    try:
        if os.path.exists(SYSTEM_DEFAULTS_PATH):
            with open(SYSTEM_DEFAULTS_PATH, 'r') as f:
                defaults = json.load(f)
            return jsonify(defaults), 200
        else:
            # If file doesn't exist, return model defaults
            # These are the same defaults defined in the User model
            model_defaults = {
                'ai_bg_color_r': 255,
                'ai_bg_color_g': 240,
                'ai_bg_color_b': 240,
                'ai_font_color_r': 51,
                'ai_font_color_g': 51,
                'ai_font_color_b': 51,
                'retry_prompt_template': '',
                'enable_markdown_prompt': False,
                'markdown_prompt_template': ''
            }
            return jsonify(model_defaults), 200 # Or 404 if you prefer to indicate 'not set by admin'
    except Exception as e:
        print(f"Error loading system default AI preferences: {e}")
        # Fallback to model defaults on error too
        model_defaults = {
            'ai_bg_color_r': 255,
            'ai_bg_color_g': 240,
            'ai_bg_color_b': 240,
            'ai_font_color_r': 51,
            'ai_font_color_g': 51,
            'ai_font_color_b': 51,
            'retry_prompt_template': '',
            'enable_markdown_prompt': False,
            'markdown_prompt_template': ''
        }
        return jsonify(model_defaults), 200 # Or 500 if a server error should be explicit
