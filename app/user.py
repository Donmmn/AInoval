from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app import db
from app.models.user import User
from app.models.group import Group
from werkzeug.security import generate_password_hash

user = Blueprint('user', __name__, url_prefix='/user')

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
