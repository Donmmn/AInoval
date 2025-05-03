from .. import db  # 从 app 包导入 db
from sqlalchemy.orm import relationship

class FileSystemItem(db.Model):
    __tablename__ = 'filesystem_items'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    item_type = db.Column(db.String(50), nullable=False) # 'folder', 'book', 'setting'
    parent_id = db.Column(db.Integer, db.ForeignKey('filesystem_items.id'), nullable=True)
    order = db.Column(db.Integer, nullable=False, default=0) # 用于同级排序
    content = db.Column(db.Text, nullable=True) # 书籍内容
    settings_data = db.Column(db.JSON, nullable=True) # 设定书内容 (使用 JSON)
    collapsed = db.Column(db.Boolean, default=True) # 文件夹折叠状态

    # 新增：关联设定书的 ID (仅用于 book 类型)
    setting_book_id = db.Column(db.Integer, db.ForeignKey('filesystem_items.id'), nullable=True)

    # 关系：获取这本书关联的设定书对象
    associated_setting = relationship(
        'FileSystemItem',
        remote_side=[id],
        foreign_keys=[setting_book_id],
        backref=db.backref('associated_books') # 可选：从设定书反查关联了哪些书
    )

    # 定义父子关系 (用于方便地查询子项)
    children = relationship(
        'FileSystemItem',
        # remote_side=[id], # 移除这个，因为它与 associated_setting 的 remote_side 冲突
        primaryjoin=('FileSystemItem.parent_id == FileSystemItem.id'), # 明确父子连接条件
        backref=db.backref('parent', remote_side=[id]),
        lazy='dynamic',
        cascade='all, delete-orphan'
    )

    def to_dict(self, include_children=False, include_setting_details=False):
        """将模型对象转换为字典，方便 API 返回。"""
        data = {
            'id': self.id,
            'name': self.name,
            'type': self.item_type,
            'parentId': self.parent_id,
            'order': self.order,
            # 根据类型决定是否包含 content/settings
            # 'content': self.content if self.item_type == 'book' else None,
            # 'settings': self.settings_data if self.item_type == 'setting' else None,
            'collapsed': self.collapsed if self.item_type == 'folder' else None,
            'settingBookId': self.setting_book_id if self.item_type == 'book' else None, # 添加关联ID
            # children 不在此处默认添加，避免无限递归，需要时手动添加
        }
        if self.item_type == 'folder' and include_children:
             # 获取子项并排序
             # 注意：这里 self.children 可能需要调整，因为 FileSystemItem 不再同一个文件
             # 可能需要显式指定 relationship 中的类名 'FileSystemItem'
             children_items = self.children.order_by(FileSystemItem.order).all()
             data['children'] = [child.to_dict(include_children=True, include_setting_details=include_setting_details) for child in children_items]
        elif self.item_type == 'setting':
             data['settings'] = self.settings_data

        # 如果需要包含关联设定书的详情
        if self.item_type == 'book' and self.associated_setting and include_setting_details:
            data['associatedSetting'] = self.associated_setting.to_dict()

        # 移除值为 None 的键，使返回更干净
        return {k: v for k, v in data.items() if v is not None}

    def __repr__(self):
        return f'<FileSystemItem {self.id}: {self.name} ({self.item_type})>' 