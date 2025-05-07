from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, current_user, logout_user, login_required
from . import db, login_manager
from .models.user import User
from .models.invitation_code import InvitationCode
import datetime

auth = Blueprint('auth', __name__)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()
        
        if user and user.verify_password(password):
            login_user(user)
            if user.password_change_required:
                flash('请更新您的初始密码。', 'warning')
                return redirect(url_for('auth.force_change_password'))
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        else:
            flash('用户名或密码错误', 'error')
            
    return render_template('login.html')

@auth.route('/logout')
def logout():
    logout_user()
    flash('您已成功退出登录。', 'success')
    return redirect(url_for('auth.login'))

@auth.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        invitation_code_str = request.form.get('invitation_code')

        # 标准用户注册逻辑（需要邀请码）
        if not username or not password or not invitation_code_str:
            flash('用户名、密码和邀请码不能为空', 'error')
            return render_template('register.html', username=username, email=email, invitation_code=invitation_code_str)
        
        inv_code = InvitationCode.query.filter_by(code=invitation_code_str).first()

        # 使用新的 is_valid 属性检查邀请码
        if not inv_code or not inv_code.is_valid:
            flash('无效或已过期的邀请码', 'error') # 合并了多个检查的提示
            return render_template('register.html', username=username, email=email, invitation_code=invitation_code_str)
        
        if User.query.filter_by(username=username).first():
            flash('用户名已存在', 'error')
            return render_template('register.html', username=username, email=email, invitation_code=invitation_code_str)
        
        if email and User.query.filter_by(email=email).first(): # 检查邮箱是否已存在
            flash('邮箱已被注册', 'error')
            return render_template('register.html', username=username, email=email, invitation_code=invitation_code_str)

        user = User(username=username, email=email if email else None, is_admin=False) # 新用户默认为非管理员
        user.password = password
        
        db.session.add(user)
        inv_code.times_used += 1 # 增加已使用次数
        db.session.add(inv_code)
        
        try:
            db.session.commit()
            flash('注册成功，请登录', 'success')
            return redirect(url_for('auth.login'))
        except Exception as e:
            db.session.rollback()
            flash(f'注册过程中发生错误: {e}', 'error')
            return render_template('register.html', username=username, email=email, invitation_code=invitation_code_str)
            
    # 对于GET请求，不再需要传递 initial_setup
    return render_template('register.html')

@auth.route('/force-change-password', methods=['GET', 'POST'])
@login_required
def force_change_password():
    if not current_user.password_change_required:
        # 如果用户不需要修改密码，则重定向到主页
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not new_password or not confirm_password:
            flash('新密码和确认密码均不能为空。', 'error')
            return render_template('auth/force_change_password.html')

        if new_password != confirm_password:
            flash('两次输入的密码不匹配。', 'error')
            return render_template('auth/force_change_password.html')
        
        if new_password == 'admin': # 或其他您想禁止的初始密码
             flash('新密码不能与初始密码相同。', 'error')
             return render_template('auth/force_change_password.html')

        current_user.password = new_password # 使用 User 模型的 password setter
        current_user.password_change_required = False
        db.session.add(current_user)
        try:
            db.session.commit()
            flash('密码已成功更新，请重新登录。', 'success')
            logout_user() # 更新密码后强制用户重新登录
            return redirect(url_for('auth.login'))
        except Exception as e:
            db.session.rollback()
            flash(f'更新密码时发生错误: {e}', 'error')
    
    return render_template('auth/force_change_password.html') 