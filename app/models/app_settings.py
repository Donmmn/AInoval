# 文件: app/models/app_settings.py
from .. import db

class AppSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f'<AppSettings {self.key}={self.value}>'

# --- 获取设置的辅助函数 ---
def get_setting(key, default=None):
    """获取应用设置值，如果键不存在则返回默认值。"""
    # 确保在应用上下文中执行查询
    from flask import current_app 
    with current_app.app_context():
        setting = AppSettings.query.filter_by(key=key).first()
    return setting.value if setting else default

# --- 设置/更新设置的辅助函数 ---
def set_setting(key, value):
    """设置或更新应用设置值。"""
    # 确保在应用上下文中执行查询和添加
    from flask import current_app
    with current_app.app_context():
        setting = AppSettings.query.filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = AppSettings(key=key, value=value)
            db.session.add(setting)
    # 注意：调用此函数后需要在外部调用 db.session.commit() 才能使更改持久化