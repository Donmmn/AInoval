from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.user import User
from app.models.group import Group
from werkzeug.security import generate_password_hash
import secrets # For random code generation
import string  # For random code generation
import datetime # For expiration_date calculation
from app.models.invitation_code import InvitationCode # Import InvitationCode model
from app.models.subscription_config import SubscriptionConfig # Import the new model

user = Blueprint('user', __name__, url_prefix='/user')

# Helper function to correct distribution_day
def _correct_distribution_day(frequency, day_value):
    if day_value is None:
        return None
    try:
        day = int(day_value)
        if frequency == 'monthly':
            return min(day, 28)
        elif frequency == 'weekly':
            return min(day, 6)
        return day # For 'daily' or other cases, return as is
    except (ValueError, TypeError):
        return day_value # Return original if not a valid number, let further validation catch it

@user.route('/manage', methods=['GET', 'POST'])
@login_required
def manage():
    if not current_user.is_admin:
        flash('无权限访问')
        return redirect(url_for('main.index'))
    users = User.query.all()
    groups = Group.query.order_by(Group.name).all()
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('用户名已存在', 'error')
        else:
            new_user = User(username=username, password=password, is_admin=False)
            db.session.add(new_user)
            db.session.commit()
            flash('用户添加成功', 'success')
        return redirect(url_for('user.manage'))
    return render_template('user_manage.html', users=users, groups=groups)

@user.route('/delete/<int:user_id>')
@login_required
def delete(user_id):
    if not current_user.is_admin:
        flash('无权限操作')
        return redirect(url_for('main.index'))
    user = User.query.get(user_id)
    if user and not user.is_admin:
        db.session.delete(user)
        db.session.commit()
        flash('用户已删除')
    else:
        flash('不能删除管理员或用户不存在')
    return redirect(url_for('user.manage'))

@user.route('/invitations', methods=['GET'])
@login_required
def get_invitations():
    if not current_user.is_admin:
        return {"error": "无权限"}, 403
    
    invitations = InvitationCode.query.order_by(InvitationCode.id.desc()).all()
    return {
        "invitations": [
            {
                "id": inv.id,
                "code": inv.code,
                "expiration_date": inv.expiration_date.strftime('%Y-%m-%d %H:%M:%S') if inv.expiration_date else None,
                "max_uses": inv.max_uses,
                "times_used": inv.times_used,
                "is_valid": inv.is_valid
            } for inv in invitations
        ]
    }, 200

def generate_random_code(length=8):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for i in range(length))

@user.route('/invitations', methods=['POST'])
@login_required
def create_invitation():
    if not current_user.is_admin:
        return {"error": "无权限"}, 403
    
    data = request.json
    if not data:
        return {"error": "请求体不能为空"}, 400
        
    code_str = data.get('code')
    is_random = data.get('is_random', False)
    expires_in_hours_str = data.get('expires_in_hours') 
    max_uses_str = data.get('max_uses')

    if is_random:
        code_str = generate_random_code()
    elif not code_str:
        return {"error": "邀请码不能为空"}, 400

    if InvitationCode.query.filter_by(code=code_str).first():
        return {"error": "邀请码已存在"}, 409

    expiration_date_obj = None
    if expires_in_hours_str is not None and expires_in_hours_str != '':
        try:
            expires_in_hours = int(expires_in_hours_str)
            if expires_in_hours <= 0:
                # Allow non-expiring by not setting expiration_date_obj or handle as error
                # For now, let's treat 0 or negative as effectively no expiration through UI default
                # Or, if a positive value is always expected for hours:
                # return {"error": "过期时间（小时）必须为正数"}, 400
                pass # Will default to 24h if this path is taken due to empty string being skipped later
            else:
                expiration_date_obj = datetime.datetime.utcnow() + datetime.timedelta(hours=expires_in_hours)
        except ValueError:
            return {"error": "过期时间（小时）格式无效"}, 400
    else: # Default to 24 hours if not provided or empty string
        expiration_date_obj = datetime.datetime.utcnow() + datetime.timedelta(hours=24)

    max_uses_val = 1
    if max_uses_str is not None and max_uses_str != '':
        try:
            max_uses_val = int(max_uses_str)
            if max_uses_val <= 0:
                return {"error": "生效次数必须为正整数"}, 400
        except ValueError:
            return {"error": "生效次数格式无效"}, 400
    
    new_inv_code = InvitationCode(
        code=code_str,
        expiration_date=expiration_date_obj,
        max_uses=max_uses_val,
        times_used=0
    )
    db.session.add(new_inv_code)
    try:
        db.session.commit()
        return {
            "message": "邀请码创建成功",
            "invitation": {
                "id": new_inv_code.id,
                "code": new_inv_code.code,
                "expiration_date": new_inv_code.expiration_date.strftime('%Y-%m-%d %H:%M:%S') if new_inv_code.expiration_date else None,
                "max_uses": new_inv_code.max_uses,
                "times_used": new_inv_code.times_used,
                "is_valid": new_inv_code.is_valid
            }
        }, 201
    except Exception as e:
        db.session.rollback()
        return {"error": f"创建邀请码失败: {str(e)}"}, 500


@user.route('/invitations/<int:code_id>', methods=['DELETE'])
@login_required
def delete_invitation(code_id):
    if not current_user.is_admin:
        return {"error": "无权限"}, 403
    
    inv_code = db.session.get(InvitationCode, code_id) # Use db.session.get for primary key lookup
    if not inv_code:
        return {"error": "邀请码不存在"}, 404
    
    db.session.delete(inv_code)
    try:
        db.session.commit()
        return {"message": "邀请码已删除"}, 200
    except Exception as e:
        db.session.rollback()
        return {"error": f"删除邀请码失败: {str(e)}"}, 500

@user.route('/invitations/cleanup', methods=['DELETE'])
@login_required
def delete_invalid_invitations():
    if not current_user.is_admin:
        return {"error": "无权限"}, 403
    
    deleted_count = 0
    try:
        all_codes = InvitationCode.query.all()
        for inv_code in all_codes:
            if not inv_code.is_valid:
                db.session.delete(inv_code)
                deleted_count += 1
        if deleted_count > 0:
            db.session.commit()
        return {"message": f"成功删除 {deleted_count} 个失效的邀请码"}, 200
    except Exception as e:
        db.session.rollback()
        return {"error": f"清理失效邀请码失败: {str(e)}"}, 500

# --- Subscription Config API Routes ---

@user.route('/subscription-configs', methods=['GET'])
@login_required
def get_subscription_configs():
    if not current_user.is_admin:
        return jsonify({"error": "无权限"}), 403
    
    configs = SubscriptionConfig.query.order_by(SubscriptionConfig.name).all()
    results = []
    for config in configs:
        results.append({
            "id": config.id,
            "name": config.name,
            "distribution_frequency": config.distribution_frequency,
            "distribution_day": config.distribution_day,
            "distribution_time": config.distribution_time.strftime('%H:%M') if config.distribution_time else None,
            "points_to_distribute": config.points_to_distribute,
            "is_active": config.is_active,
            "last_processed_at": config.last_processed_at.strftime('%Y-%m-%d %H:%M:%S') if config.last_processed_at else None,
            "target_groups": [{"id": g.id, "name": g.name} for g in config.target_groups.all()]
        })
    return jsonify(results), 200

@user.route('/subscription-configs', methods=['POST'])
@login_required
def create_subscription_config():
    if not current_user.is_admin:
        return jsonify({"error": "无权限"}), 403
    
    i = 1
    while True:
        default_name = f"默认订阅组 {i}"
        if not SubscriptionConfig.query.filter_by(name=default_name).first():
            break
        i += 1
            
    new_config = SubscriptionConfig(
        name=default_name,
        distribution_frequency='monthly',
        distribution_day=1,
        distribution_time=datetime.time(0,0),
        points_to_distribute=0,
        is_active=False
    )
    db.session.add(new_config)
    try:
        db.session.commit()
        return jsonify({
            "id": new_config.id,
            "name": new_config.name,
            "distribution_frequency": new_config.distribution_frequency,
            "distribution_day": new_config.distribution_day,
            "distribution_time": new_config.distribution_time.strftime('%H:%M'),
            "points_to_distribute": new_config.points_to_distribute,
            "is_active": new_config.is_active,
            "target_groups": []
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"创建订阅配置失败: {str(e)}"}), 500

@user.route('/subscription-configs/<int:config_id>', methods=['GET'])
@login_required
def get_subscription_config_detail(config_id):
    if not current_user.is_admin:
        return jsonify({"error": "无权限"}), 403
    config = db.session.get(SubscriptionConfig, config_id)
    if not config:
        return jsonify({"error": "订阅配置未找到"}), 404
    
    return jsonify({
        "id": config.id,
        "name": config.name,
        "distribution_frequency": config.distribution_frequency,
        "distribution_day": config.distribution_day,
        "distribution_time": config.distribution_time.strftime('%H:%M') if config.distribution_time else None,
        "points_to_distribute": config.points_to_distribute,
        "is_active": config.is_active,
        "last_processed_at": config.last_processed_at.strftime('%Y-%m-%d %H:%M:%S') if config.last_processed_at else None,
        "target_groups": [{"id": g.id, "name": g.name} for g in config.target_groups.all()]
    }), 200


@user.route('/subscription-configs/<int:config_id>', methods=['PUT'])
@login_required
def update_subscription_config(config_id):
    if not current_user.is_admin:
        return jsonify({"error": "无权限"}), 403
    
    config = db.session.get(SubscriptionConfig, config_id)
    if not config:
        return jsonify({"error": "订阅配置未找到"}), 404
        
    data = request.json
    if not data:
        return jsonify({"error": "缺少数据"}), 400

    try:
        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({"error": "名称不能为空"}), 400
            existing_config = SubscriptionConfig.query.filter(SubscriptionConfig.name == new_name, SubscriptionConfig.id != config_id).first()
            if existing_config:
                return jsonify({"error": "该名称已被其他订阅组使用"}), 409
            config.name = new_name
            
        current_frequency = config.distribution_frequency # Store current frequency before potential update
        if 'distribution_frequency' in data:
            freq = data['distribution_frequency']
            if freq not in ['daily', 'weekly', 'monthly']:
                return jsonify({"error": "无效的发放频率"}), 400
            config.distribution_frequency = freq
            current_frequency = freq # Update current_frequency if it's changed in this request

        if 'distribution_day' in data:
            day_val_from_request = data.get('distribution_day')
            
            # Correct the day value based on the potentially updated frequency
            corrected_day_val = _correct_distribution_day(current_frequency, day_val_from_request)

            try:
                if corrected_day_val is None and current_frequency != 'daily':
                    # This case should ideally be handled by frontend or clearer requirements
                    # For now, if day is None for weekly/monthly after correction (e.g. invalid input),
                    # we might rely on existing validation or decide if it's an error.
                    # If corrected_day_val became None due to invalid input to _correct_distribution_day,
                    # the int() conversion below would fail if not caught by _correct_distribution_day returning original.
                    # Let's assume if corrected_day_val is None here, it means it was intentionally set to None for daily.
                     pass # Allow None if it's for daily or if correction resulted in None (e.g. from invalid input)
                elif corrected_day_val is not None:
                    day = int(corrected_day_val) # Ensure it's an int after correction
                    # Validation based on the *current_frequency* (which might have just been updated)
                    if current_frequency == 'monthly':
                        if not (1 <= day <= 28): # Max is 28 after correction
                             return jsonify({"error": "月份日期在修正后必须在1-28之间"}), 400
                    elif current_frequency == 'weekly':
                        if not (0 <= day <= 6): # Max is 6 after correction
                             return jsonify({"error": "星期几在修正后必须在0-6之间"}), 400
                    # No specific day range validation for 'daily' if day is not None (though typically it would be)
                    config.distribution_day = day
                else: # corrected_day_val is None (likely for 'daily' frequency)
                     config.distribution_day = None
            except (ValueError, TypeError):
                 return jsonify({"error": "日期/星期格式无效或修正后仍不符合要求"}), 400
        
        if 'distribution_time' in data and data['distribution_time'] is not None:
            try:
                time_obj = datetime.datetime.strptime(data['distribution_time'], '%H:%M').time()
                config.distribution_time = time_obj
            except (ValueError, TypeError):
                return jsonify({"error": "时间格式无效 (HH:MM)"}), 400

        if 'points_to_distribute' in data and data['points_to_distribute'] is not None:
            try:
                points = int(data['points_to_distribute'])
                if points < 0:
                    return jsonify({"error": "点数不能为负"}), 400
                config.points_to_distribute = points
            except (ValueError, TypeError):
                return jsonify({"error": "点数格式无效"}), 400
        
        if 'is_active' in data and isinstance(data['is_active'], bool):
            config.is_active = data['is_active']

        if 'target_group_ids' in data and isinstance(data['target_group_ids'], list):
            # Efficiently update many-to-many relationship
            current_target_groups = {group.id for group in config.target_groups}
            new_target_group_ids = set(data['target_group_ids'])

            ids_to_remove = current_target_groups - new_target_group_ids
            ids_to_add = new_target_group_ids - current_target_groups

            for group_id_to_remove in ids_to_remove:
                group = db.session.get(Group, group_id_to_remove)
                if group and group in config.target_groups:
                    config.target_groups.remove(group)
            
            for group_id_to_add in ids_to_add:
                group = db.session.get(Group, group_id_to_add)
                if group and group not in config.target_groups:
                    config.target_groups.append(group)

        db.session.commit()
        return jsonify({
            "id": config.id, "name": config.name, "distribution_frequency": config.distribution_frequency,
            "distribution_day": config.distribution_day, 
            "distribution_time": config.distribution_time.strftime('%H:%M') if config.distribution_time else None,
            "points_to_distribute": config.points_to_distribute, "is_active": config.is_active,
            "target_groups": [{"id": g.id, "name": g.name} for g in config.target_groups.all()]
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"更新订阅配置失败: {str(e)}"}), 500


@user.route('/subscription-configs/<int:config_id>', methods=['DELETE'])
@login_required
def delete_subscription_config(config_id):
    if not current_user.is_admin:
        return jsonify({"error": "无权限"}), 403
    
    config = db.session.get(SubscriptionConfig, config_id)
    if not config:
        return jsonify({"error": "订阅配置未找到"}), 404
        
    try:
        db.session.delete(config)
        db.session.commit()
        return jsonify({"message": "订阅配置已删除"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"删除订阅配置失败: {str(e)}"}), 500
