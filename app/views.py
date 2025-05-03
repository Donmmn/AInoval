from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, logout_user, current_user

main = Blueprint('main', __name__)

@main.route('/')
@login_required
def index():
    auto_save_setting = current_user.auto_save_on_navigate
    user_points = current_user.points
    return render_template('index.html', user_auto_save_setting=auto_save_setting, user_points=user_points)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login')) 