# -*- coding: utf-8 -*-
import glob
import os

files = glob.glob('templates/reservar_*.html')
old_logic = '''                    const result = await response.json();
                    if (result.status === 'success') {
                        // Pequeña pausa para que se vea el procesamiento
                        const wasAdmin = !!window.adminCreacionInfo;
                        window.adminCreacionInfo = null;
                        setTimeout(() => {
                            window.location.href = wasAdmin ? location.pathname : "{% url 'mis_reservas' %}";
                        }, 500);
                    }'''

new_logic = '''                    const result = await response.json();
                    if (result.status === 'success') {
                        // Procesamiento completado
                        const wasAdmin = !!window.adminCreacionInfo;
                        window.adminCreacionInfo = null;
                        if (!wasAdmin && selectedPaymentMethod === 'tarjeta' && result.reserva_id) {
                            window.location.href = /webpay/iniciar/ + result.reserva_id + /;
                        } else {
                            setTimeout(() => {
                                window.location.href = wasAdmin ? location.pathname : "{% url 'mis_reservas' %}";
                            }, 500);
                        }
                    }'''

for fpath in files:
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Intento de replace normal, asumiendo que los espacios coinciden
    new_content = content.replace(old_logic, new_logic)
    
    # Si no coinciden, fallamos ruidosamente
    if new_content == content:
        # Intento normalizando los saltos de línea (CRLF vs LF)
        old_logic_normalized = old_logic.replace('\r\n', '\n')
        new_logic_normalized = new_logic.replace('\r\n', '\n')
        content_normalized = content.replace('\r\n', '\n')
        new_content = content_normalized.replace(old_logic_normalized, new_logic_normalized)
        
        if new_content == content_normalized:
            # Probemos usando replace ignorando la indentación exacta
            print(f"FAILED to replace in {fpath}")
            continue

    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"Updated {fpath}")
