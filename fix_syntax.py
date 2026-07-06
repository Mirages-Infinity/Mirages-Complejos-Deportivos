# -*- coding: utf-8 -*-
import glob

files = glob.glob('templates/reservar_*.html')
for fpath in files:
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Arreglar la sintaxis de comillas perdidas por el backtick de powershell
    bad_code = "window.location.href = /webpay/iniciar/ + result.reserva_id + /;"
    good_code = "window.location.href = '/webpay/iniciar/' + result.reserva_id + '/';"
    
    if bad_code in content:
        content = content.replace(bad_code, good_code)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed {fpath}")
