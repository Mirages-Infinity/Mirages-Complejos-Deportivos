from django import template

register = template.Library()


@register.filter(name='clp')
def clp(value):
    """Formatea un número como precio en pesos chilenos: $ 10.000"""
    try:
        n = int(value)
        return f'$ {n:,}'.replace(',', '.')
    except (ValueError, TypeError):
        return '$ 0'
