# -*- coding: utf-8 -*-
with open('reservas/views.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_logic = '''    if tbk_token and not token:
        # Usuario abortó el pago
        transaccion = TransaccionWebpay.objects.filter(token_ws=tbk_token).first()
        if transaccion:
            transaccion.estado = 'ANULADA'
            transaccion.save()
            messages.warning(request, 'Cancelaste el pago en Webpay.')
            return redirect('mis_reservas')'''

new_logic = '''    if tbk_token and not token:
        # Usuario abortó el pago
        transaccion = TransaccionWebpay.objects.filter(token_ws=tbk_token).first()
        if transaccion:
            transaccion.estado = 'ANULADA'
            transaccion.save()
            
            # Liberar la reserva
            reserva = transaccion.reserva
            reserva.estado = 'RECHAZADA'
            reserva.save()
            
            messages.warning(request, 'Cancelaste el pago en Webpay y la reserva fue anulada.')
            return redirect('mis_reservas')'''
            
content = content.replace(old_logic, new_logic)

old_logic_fail = '''        else:
            transaccion.estado = 'RECHAZADA'
            transaccion.save()
            messages.error(request, 'El pago fue rechazado por Transbank.')
            return redirect('mis_reservas')'''

new_logic_fail = '''        else:
            transaccion.estado = 'RECHAZADA'
            transaccion.save()
            
            # Liberar la reserva
            reserva = transaccion.reserva
            reserva.estado = 'RECHAZADA'
            reserva.save()
            
            messages.error(request, 'El pago fue rechazado por Transbank y la reserva fue anulada.')
            return redirect('mis_reservas')'''

content = content.replace(old_logic_fail, new_logic_fail)

old_logic_exc = '''        logger.exception("Error al confirmar transacción Webpay")
        transaccion.estado = 'FALLIDA'
        transaccion.save()
        messages.error(request, 'Ocurrió un error al verificar tu pago con Transbank. Contáctanos si el cargo fue realizado.')
        return redirect('mis_reservas')'''

new_logic_exc = '''        logger.exception("Error al confirmar transacción Webpay")
        if transaccion:
            transaccion.estado = 'FALLIDA'
            transaccion.save()
            reserva = transaccion.reserva
            reserva.estado = 'RECHAZADA'
            reserva.save()
        messages.error(request, 'Ocurrió un error al verificar tu pago con Transbank. Contáctanos si el cargo fue realizado.')
        return redirect('mis_reservas')'''

content = content.replace(old_logic_exc, new_logic_exc)

with open('reservas/views.py', 'w', encoding='utf-8') as f:
    f.write(content)
