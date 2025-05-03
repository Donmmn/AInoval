from .. import db  # 假设 db 在 app/__init__.py 中定义
from datetime import datetime
from .user import User # 需要导入 User

class PromptTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False) # 模板名称，不再要求 unique
    template_string = db.Column(db.Text, nullable=False) # 模板内容
    is_default = db.Column(db.Boolean, default=False, nullable=False) # 是否为默认模板 (可能主要用于系统模板)

    # ---> 新增：关联用户 <--- 
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # Nullable 表示系统模板
    owner = db.relationship('User', backref=db.backref('prompt_templates', lazy=True)) # 添加关系

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # --- 添加唯一约束和索引 --- 
    __table_args__ = (
        db.UniqueConstraint('user_id', 'name', name='uq_user_template_name'), # 用户 ID + 模板名 唯一
        db.Index('ix_prompttemplate_user_id_name', 'user_id', 'name'), # 索引优化查询
        # 注意：系统模板名称唯一性需要在应用层或使用特定数据库约束处理
    )

    def __repr__(self):
        owner_info = f"User {self.user_id}" if self.user_id else "System"
        return f'<PromptTemplate {self.id}: {self.name} ({owner_info})>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'template_string': self.template_string,
            'is_default': self.is_default,
            'user_id': self.user_id, # <--- 返回 user_id
            'owner_username': self.owner.username if self.owner else None, # <--- 返回用户名 (如果有关联)
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        } 