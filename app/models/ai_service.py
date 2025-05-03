from .. import db
from sqlalchemy.orm import relationship

class AIService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False) # e.g., "Default GPT-4", "My Personal Claude"
    service_type = db.Column(db.String(50), nullable=False) # e.g., 'openai', 'claude', 'ollama', 'google'
    
    # --- Sensitive Information ---
    # SECURITY WARNING: Storing API keys directly in the database is generally insecure.
    # Consider using environment variables, a secrets manager (like Hashicorp Vault, AWS/GCP Secrets Manager),
    # or at least database-level encryption if your DB supports it.
    # For simplicity in this example, we store it as a string.
    api_key = db.Column(db.String(255), nullable=True) # Nullable for system service if key stored elsewhere? Or required for user service.
    
    # --- Configuration ---
    base_url = db.Column(db.String(255), nullable=True) # Optional: For custom endpoints (Ollama, vLLM, etc.)
    model_name = db.Column(db.String(100), nullable=True) # e.g., 'gpt-4-turbo', 'claude-3-opus-20240229', 'llama3'
    
    # --- Ownership and Type ---
    is_system_service = db.Column(db.Boolean, default=False, nullable=False) # True if configured by admin for system use
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # Link to user if not a system service
    # --- 新增：标记是否为系统默认服务 ---
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    # --- 新增：是否启用流式响应 ---
    enable_streaming = db.Column(db.Boolean, nullable=False, default=True)
    
    # Relationship to User (if it's a user-owned service)
    # Specify foreign_keys explicitly due to multiple FK paths between User and AIService
    owner = relationship("User", foreign_keys=[owner_id], backref="ai_services") # User can have multiple AIService configs

    def __repr__(self):
        service_scope = "System" if self.is_system_service else f"User ({self.owner_id})"
        return f'<AIService {self.id}: {self.name} ({self.service_type}) - {service_scope}>'

    # Optional: Add a method to return a dictionary representation
    def to_dict(self, reveal_key=False):
        data = {
            'id': self.id,
            'name': self.name,
            'service_type': self.service_type,
            'base_url': self.base_url,
            'model_name': self.model_name,
            'is_system_service': self.is_system_service,
            'owner_id': self.owner_id,
            'is_default': self.is_default,
            'enable_streaming': self.enable_streaming
            # IMPORTANT: Never return the api_key by default in to_dict unless explicitly needed and secured.
        }
        # Only include key if specifically requested (e.g., by the owner or admin for management)
        # This 'reveal_key' flag needs careful handling in the API layer.
        if reveal_key and self.api_key:
             data['api_key'] = self.api_key # Use with extreme caution!
        return data 