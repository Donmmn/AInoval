from .. import db
import datetime

class InvitationCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False)
    expiration_date = db.Column(db.DateTime, nullable=True) # 可选的过期时间
    # is_used = db.Column(db.Boolean, default=False, nullable=False) # 将被 max_uses 和 times_used 取代
    max_uses = db.Column(db.Integer, default=1, nullable=False) # 最大使用次数，默认为1
    times_used = db.Column(db.Integer, default=0, nullable=False) # 已使用次数，默认为0

    # 记录是谁创建了这个邀请码，可选
    # created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # creator = db.relationship('User', backref=db.backref('created_codes', lazy=True))

    @property
    def is_valid(self):
        """检查邀请码是否仍然有效"""
        if self.times_used >= self.max_uses:
            return False
        if self.expiration_date and self.expiration_date < datetime.datetime.utcnow():
            return False
        return True

    def __repr__(self):
        return f'<InvitationCode {self.code} (Uses: {self.times_used}/{self.max_uses}, Expires: {self.expiration_date})>' 