# -*- coding: utf-8 -*-
import os
import glob
import time

history_dir = r"C:\Users\Informatica\AppData\Roaming\Code\User\History"
search_str = "{% block title %}Reserva Estadio Municipal - Complejos Deportivos{% endblock %}"

matches = []
for root, dirs, files in os.walk(history_dir):
    for file in files:
        if file == 'entries.json':
            continue
        path = os.path.join(root, file)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                if search_str in content:
                    mtime = os.path.getmtime(path)
                    matches.append((path, mtime, content))
        except Exception:
            pass

matches.sort(key=lambda x: x[1], reverse=True)
print(f"Found {len(matches)} historical versions of reservar_estadio.html")

if matches:
    print(f"Latest version was saved at: {time.ctime(matches[0][1])}")
    # Write the latest one to a temp file so we can inspect it
    with open('latest_history_estadio.html', 'w', encoding='utf-8') as f:
        f.write(matches[0][2])
    print("Saved latest to latest_history_estadio.html")
