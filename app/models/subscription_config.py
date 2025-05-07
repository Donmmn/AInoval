from .. import db
import datetime

# 关联表：SubscriptionConfig 与 Group
subscription_config_group_association = db.Table('subscription_config_group_association',
    db.Column('subscription_config_id', db.Integer, db.ForeignKey('subscription_config.id'), primary_key=True),
    db.Column('group_id', db.Integer, db.ForeignKey('group.id'), primary_key=True)
)

class SubscriptionConfig(db.Model):
    __tablename__ = 'subscription_config'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True) # 订阅配置名称

    # 发放频率 ('daily', 'weekly', 'monthly')
    distribution_frequency = db.Column(db.String(20), nullable=False, default='monthly')
    
    # 发放日期/星期:
    # - monthly: day of month (1-31)
    # - weekly: day of week (0=Monday, 1=Tuesday, ..., 6=Sunday)
    # - daily: null
    distribution_day = db.Column(db.Integer, nullable=True) 
    
    # 发放时间 (HH:MM)
    distribution_time = db.Column(db.Time, nullable=False, default=datetime.time(0, 0)) # 默认为午夜
    
    points_to_distribute = db.Column(db.Integer, nullable=False, default=0) # 每次发放的点数
    is_active = db.Column(db.Boolean, default=True, nullable=False) # 是否启用

    # 记录上次成功执行的时间，用于调度逻辑
    last_processed_at = db.Column(db.DateTime, nullable=True) 

    # 多对多关系: 哪些用户组应用此订阅
    target_groups = db.relationship('Group', 
                                    secondary=subscription_config_group_association,
                                    lazy='dynamic', # 返回查询对象
                                    backref=db.backref('subscription_configs', lazy=True))

    def __repr__(self):
        return f'<SubscriptionConfig {self.name} ({self.distribution_frequency})>'

    def get_distribution_details_display(self):
        details = f"{self.distribution_frequency.capitalize()} "
        if self.distribution_frequency == 'monthly':
            details += f"on day {self.distribution_day} "
        elif self.distribution_frequency == 'weekly':
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            details += f"on {days[self.distribution_day] if self.distribution_day is not None and 0 <= self.distribution_day <= 6 else 'N/A'} "
        details += f"at {self.distribution_time.strftime('%H:%M') if self.distribution_time else 'N/A'}"
        return details 