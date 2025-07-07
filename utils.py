# app_web/utils.py

def letra_por_indice(i):
    res = ''
    while True:
        res = chr(ord('A') + (i % 26)) + res
        i = i // 26 - 1
        if i < 0:
            break
    return res
