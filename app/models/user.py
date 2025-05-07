from .. import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .group import user_group_association

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    password_change_required = db.Column(db.Boolean, default=False, nullable=False)
    auto_save_on_navigate = db.Column(db.Boolean, default=True, nullable=False)
    points = db.Column(db.Integer, default=0, nullable=False)
    # --- 新增字段：存储用户启用的 AI 服务配置 ID ---
    active_ai_service_id = db.Column(db.Integer, db.ForeignKey('ai_service.id', use_alter=True, name='fk_user_active_ai_service'), nullable=True)
    # --- 可选：添加关系以方便访问活动配置对象 (如果需要) ---
    active_ai_service = db.relationship('AIService', foreign_keys=[active_ai_service_id], post_update=True)

    # 与 Group 的多对多关系
    groups = db.relationship('Group', 
                             secondary=user_group_association, 
                             lazy='dynamic', # 返回查询对象
                             back_populates='users')

    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>' 