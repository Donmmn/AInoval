import os # Ensure this import is present at the top

# ... existing code ...

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core.apps.CoreConfig', # Use AppConfig for better practice
]

# ... existing code ...

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')], # Add templates directory
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            # Add the custom template tags built-in processor
            'builtins': ['core.templatetags.core_tags'],
        },
    },
]

# ... existing code ...

# Add these lines at the end of the file
LOGIN_REDIRECT_URL = '/' # Redirect to dashboard after login
LOGOUT_REDIRECT_URL = '/accounts/login/' # Redirect to login page after logout 