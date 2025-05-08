from app import db
from sqlalchemy.sql import func

class ApiCallLog(db.Model):
    __tablename__ = 'api_call_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(150), nullable=False) # Denormalized for easier querying/display
    timestamp = db.Column(db.DateTime(timezone=True), server_default=func.now())
    ai_service_id = db.Column(db.Integer, db.ForeignKey('ai_service.id'), nullable=True) # Link to the AI service used
    ai_service_name = db.Column(db.String(150), nullable=False)
    is_system_service = db.Column(db.Boolean, default=False, nullable=False)
    tokens_consumed = db.Column(db.Integer, nullable=False, default=0)
    points_deducted = db.Column(db.Integer, nullable=False, default=0)
    prompt_length = db.Column(db.Integer, nullable=True) # Optional: length of the prompt
    response_length = db.Column(db.Integer, nullable=True) # Optional: length of the response

    user = db.relationship('User', backref=db.backref('api_calls', lazy='dynamic'))
    ai_service = db.relationship('AIService') # Optional: if you want to easily navigate to service details

    def __repr__(self):
        return f'<ApiCallLog {self.id} by {self.username} at {self.timestamp}>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'ai_service_id': self.ai_service_id,
            'ai_service_name': self.ai_service_name,
            'is_system_service': self.is_system_service,
            'tokens_consumed': self.tokens_consumed,
            'points_deducted': self.points_deducted,
            'prompt_length': self.prompt_length,
            'response_length': self.response_length
        } 