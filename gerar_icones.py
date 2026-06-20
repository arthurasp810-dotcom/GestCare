"""Gera os ícones do PWA (manifest.json) a partir de formas vetoriais simples.
Execute uma vez (ou de novo se quiser trocar a cor/desenho): python gerar_icones.py
"""
import os
import math
from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(__file__), 'static', 'icons')
os.makedirs(OUT_DIR, exist_ok=True)

COR_FUNDO = (45, 106, 122, 255)      # #2d6a7a — mesma cor do theme_color
COR_CORACAO = (255, 255, 255, 255)   # branco


def pontos_coracao(cx, cy, escala, n=200):
    """Curva matemática clássica do coração, normalizada e centrada."""
    pts = []
    for i in range(n):
        t = (i / n) * 2 * math.pi
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((x, y))
    max_x = max(abs(p[0]) for p in pts)
    max_y = max(abs(p[1]) for p in pts)
    fator = escala / max(max_x, max_y)
    # y da curva cresce "para cima" (matemático); na imagem y cresce para baixo, então inverte
    return [(cx + x * fator, cy - y * fator) for x, y in pts]


def gerar(tamanho, nome_arquivo, raio_relativo=0.22, escala_coracao=0.34, fundo_quadrado=False):
    img = Image.new('RGBA', (tamanho, tamanho), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if fundo_quadrado:
        draw.rectangle([0, 0, tamanho, tamanho], fill=COR_FUNDO)
    else:
        raio = int(tamanho * raio_relativo)
        draw.rounded_rectangle([0, 0, tamanho, tamanho], radius=raio, fill=COR_FUNDO)

    cx, cy = tamanho / 2, tamanho / 2
    escala = tamanho * escala_coracao
    draw.polygon(pontos_coracao(cx, cy, escala), fill=COR_CORACAO)

    img.save(os.path.join(OUT_DIR, nome_arquivo))
    print(f'Gerado: {nome_arquivo} ({tamanho}x{tamanho})')


# Ícones "any" — podem ter as bordas levemente arredondadas, coração ocupando mais espaço
gerar(192, 'icon-192.png', raio_relativo=0.18, escala_coracao=0.34)
gerar(512, 'icon-512.png', raio_relativo=0.18, escala_coracao=0.34)

# Ícones "maskable" — fundo até a borda + coração menor (margem de segurança p/ recorte do SO)
gerar(192, 'icon-maskable-192.png', fundo_quadrado=True, escala_coracao=0.24)
gerar(512, 'icon-maskable-512.png', fundo_quadrado=True, escala_coracao=0.24)

# Apple touch icon — iOS já aplica seu próprio recorte/sombra, fundo precisa cobrir tudo
gerar(180, 'apple-touch-icon.png', fundo_quadrado=True, escala_coracao=0.32)

# Favicon simples
gerar(32, 'favicon-32.png', raio_relativo=0.18, escala_coracao=0.34)

print('Pronto! Ícones salvos em static/icons/')
