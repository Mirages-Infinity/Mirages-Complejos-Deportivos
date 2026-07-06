# -*- coding: utf-8 -*-
import glob
import os
import re

files = glob.glob('templates/reservar_*.html')

for fpath in files:
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # We will use regex to replace the success block to handle any whitespace
    pattern = re.compile(r"if \(result\.status === 'success'\) \{[\s\S]*?window\.location\.href = wasAdmin \? location\.pathname : \"\{\% url 'mis_reservas' \%\}\";\s*\}, 500\);\s*\}")
    
    new_logic = '''if (result.status === 'success') {
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
                    
    content = pattern.sub(new_logic, content)
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Updated {fpath}")
