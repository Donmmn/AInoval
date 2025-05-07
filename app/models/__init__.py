# Import models here to make them available via app.models
# Assuming User model is in app/models/user.py
try:
    from .user import User, UserRole, Role
except ImportError:
    # Handle case where user.py might not exist or User isn't defined yet
    pass 

# Assuming FileSystemItem model will be in app/models/filesystem.py
try:
    from .filesystem import FileSystemItem, File, Folder
except ImportError:
    pass

# You might also need to import db here if models use it directly at definition time,
# though typically it's imported within model files from the main app package.
# from .. import db 

from .ai_service import AIService
from .group import Group
from .prompt import PromptTemplate
from .invitation_code import InvitationCode
from .subscription_config import SubscriptionConfig, subscription_config_group_association

__all__ = [
    'User', 'UserRole', 'Role',
    'Group',
    'File', 'Folder',
    'AIService',
    'PromptTemplate',
    'InvitationCode',
    'SubscriptionConfig',
    'subscription_config_group_association'
] 
# 文件: app/models/__init__.py
# ... (可能存在的其他导入) ...
from .app_settings import AppSettings, get_setting, set_setting # 确保这行存在