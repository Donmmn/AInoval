from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    """Allows accessing dictionary values by variable key in templates."""
    return dictionary.get(key) 