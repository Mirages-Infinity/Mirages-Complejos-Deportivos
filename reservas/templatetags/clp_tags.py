from django import template
import re

register = template.Library()


@register.filter(name='clp')
def clp(value):
    try:
        n = int(value)
        return f'$ {n:,}'.replace(',', '.')
    except (ValueError, TypeError):
        return '$ 0'


@register.filter(name='rut')
def rut(value):
    """21029426-0  →  21.029.426-0"""
    if not value:
        return value
    s = str(value).strip().upper()
    if '-' not in s:
        return s
    cuerpo, dv = s.rsplit('-', 1)
    cuerpo = cuerpo.replace('.', '')
    try:
        cuerpo_fmt = f'{int(cuerpo):,}'.replace(',', '.')
    except ValueError:
        return s
    return f'{cuerpo_fmt}-{dv}'


@register.filter(name='telefono')
def telefono(value):
    """938797345  →  +56 9 3879 7345"""
    if not value:
        return value
    digits = re.sub(r'\D', '', str(value))
    if digits.startswith('56'):
        digits = digits[2:]
    if len(digits) == 9 and digits.startswith('9'):
        return f'+56 {digits[0]} {digits[1:5]} {digits[5:]}'
    if len(digits) == 8:
        return f'+56 {digits[:4]} {digits[4:]}'
    return f'+56 {digits}'
