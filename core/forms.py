from django import forms
from .models import NovelProject, Template

class NovelProjectForm(forms.ModelForm):
    class Meta:
        model = NovelProject
        fields = ['title'] # Only allow user to input title
        labels = {
            'title': '项目名称'
        }
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '输入小说项目名称'}),
        }

class TemplateForm(forms.ModelForm):
    class Meta:
        model = Template
        fields = ['name', 'outline', 'style_prompt']
        labels = {
            'name': '模板名称',
            'outline': '小说大纲',
            'style_prompt': '文风提示',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '输入模板名称'}),
            'outline': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': '可选，输入小说大纲...'}),
            'style_prompt': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': '可选，输入文风提示...'}),
        } 