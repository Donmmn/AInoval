from .. import db

# 关联表定义 (放在模型定义之前或内部)
user_group_association = db.Table('user_group_association',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('group.id'), primary_key=True)
)

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    
    # 反向关系定义 (通过关联表)
    # users = db.relationship('User', secondary=user_group_association, back_populates='groups') 
    # 注意：back_populates 需要在 User 模型中也定义
    # secondary 指定了关联表
    # lazy='dynamic' 可以让 users 属性返回一个查询对象，而不是直接加载所有用户，对大组有用
    users = db.relationship('User', 
                            secondary=user_group_association, 
                            lazy='dynamic', 
                            back_populates='groups') 

    def __repr__(self):
        return f'<Group {self.name}>' 