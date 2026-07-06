with open('reservas/views.py', 'r', encoding='utf-8') as f:
    content = f.read()
old_code = '''            if metodo_pago == 'CUPON':
                estado = 'APROBADA'
            elif metodo_pago == 'TARJETA':
                estado = 'APROBADA'
            else:
                estado = 'PENDIENTE'''
new_code = '''            if metodo_pago == 'CUPON':
                estado = 'APROBADA'
            elif metodo_pago == 'TARJETA':
                estado = 'PENDIENTE'
            else:
                estado = 'PENDIENTE'''
content = content.replace(old_code, new_code)
with open('reservas/views.py', 'w', encoding='utf-8') as f:
    f.write(content)
