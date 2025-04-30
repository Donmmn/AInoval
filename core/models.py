from django.db import models
from django.contrib.auth.models import User # Import User model
import os

# Novel Project Model
class NovelProject(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE) # Link to user
    title = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

# Template Model
class Template(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE) # Link to user
    name = models.CharField(max_length=100)
    outline = models.TextField(blank=True, null=True) # Novel outline
    style_prompt = models.TextField(blank=True, null=True) # Style prompt
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name 