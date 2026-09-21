#!/usr/bin/env python3
"""
Monitor de criptomoedas com alerta no celular (via app ntfy).

Avisa quando a variação das últimas 24h de uma moeda:
  - cair 2% ou mais   (variação <= -2,00%)
  - subir 3% ou mais  (variação >= +3,00%)

Envia UM aviso quando a moeda entra na zona de alerta e só avisa de novo
depois que ela volta para a faixa normal (evita notificação repetida a cada 5 min).
Não precisa instalar nada: usa só a biblioteca padrão do Python.
"""

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# ================== CONFIGURAÇÃO (pode editar) ==================
LIMITE_QUEDA = -2.0   # avisa se a variação 24h for <= -2%
LIMITE_ALTA = 3.0     # avisa se a variação 24h for >= +3%
HISTERESE = 0.5       # quanto precisa voltar (em pontos %) para rearmar o aviso

MOEDAS = {
    # id CoinGecko: (nome, símbolo, par na Coinbase usado como fonte reserva)
    "bitcoin":  ("Bitcoin",  "BTC", "BTC-USD"),
    "ethereum": ("Ethereum", "ETH", "ETH-USD"),
    "solana":   ("Solana",   "SOL", "SOL-USD"),
    "litecoin": ("Litecoin", "LTC", "LTC-USD"),
}
# ================================================================

NTFY_SERVIDOR = os.environ.get("NTFY_SERVIDOR", "https://ntfy.sh").rstrip("/") + "/"
NTFY_TOPICO = os.environ.get("NTFY_TOPICO", "").strip()
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "").strip()
MODO_TESTE = os.environ.get("TESTE", "").strip().lower() in ("1", "true", "sim")
ARQUIVO_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado.json")
FUSO_BRASILIA = timezone(timedelta(hours=-3))
USER_AGENT = "monitor-cripto/1.0"


def buscar_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def precos_coingecko():
    url = ("https://api.coingecko.com/api/v3/simple/price"
           f"?ids={','.join(MOEDAS)}&vs_currencies=usd,brl&include_24hr_change=true")
    if COINGECKO_API_KEY:
        url += f"&x_cg_demo_api_key={COINGECKO_API_KEY}"
    dados = buscar_json(url)
    precos = {}
    for moeda in MOEDAS:
        d = dados[moeda]
        precos[moeda] = {
            "variacao": float(d["usd_24h_change"]),
            "usd": float(d["usd"]),
            "brl": float(d["brl"]) if d.get("brl") is not None else None,
        }
    return precos


def precos_coinbase():
    precos = {}
    for moeda, (_, _, par) in MOEDAS.items():
        d = buscar_json(f"https://api.exchange.coinbase.com/products/{par}/stats")
        abertura, ultimo = float(d["open"]), float(d["last"])
        precos[moeda] = {"variacao": (ultimo - abertura) / abertura * 100, "usd": ultimo, "brl": None}
    return precos


def zona_para(variacao, zona_anterior):
    if variacao <= LIMITE_QUEDA:
        return "queda"
    if variacao >= LIMITE_ALTA:
        return "alta"
    # Faixa normal: só "rearma" depois de se afastar do limite (evita avisos em sequência)
    if zona_anterior == "queda" and variacao <= LIMITE_QUEDA + HISTERESE:
        return "queda"
    if zona_anterior == "alta" and variacao >= LIMITE_ALTA - HISTERESE:
        return "alta"
    return "normal"


def numero_br(valor, casas=2):
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def pct_br(valor):
    return f"{valor:+.2f}%".replace(".", ",")


def linha_preco(p):
    casas = 2 if p["usd"] >= 1 else 4
    partes = []
    if p["brl"] is not None:
        partes.append(f"R$ {numero_br(p['brl'], casas)}")
    partes.append(f"US$ {numero_br(p['usd'], casas)}")
    return " | ".join(partes)


def enviar_notificacao(titulo, mensagem, tags, prioridade=4, link=None):
    if not NTFY_TOPICO:
        print(f"[NTFY_TOPICO não configurado] {titulo} — {mensagem}")
        return
    payload = {"topic": NTFY_TOPICO, "title": titulo, "message": mensagem,
               "tags": tags, "priority": prioridade}
    if link:
        payload["click"] = link
    req = urllib.request.Request(
        NTFY_SERVIDOR,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()
    print(f"Notificação enviada: {titulo}")


def carregar_estado():
    try:
        with open(ARQUIVO_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def salvar_estado(estado):
    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2, sort_keys=True)
        f.write("\n")


def main():
    try:
        precos, fonte = precos_coingecko(), "CoinGecko"
    except Exception as erro:
        print(f"CoinGecko indisponível ({erro}); usando Coinbase.")
        precos, fonte = precos_coinbase(), "Coinbase"

    agora = datetime.now(FUSO_BRASILIA).strftime("%d/%m %H:%M")
    estado = carregar_estado()
    novo_estado = {}
    resumo = []

    for moeda, (nome, simbolo, _) in MOEDAS.items():
        p = precos[moeda]
        anterior = estado.get(moeda, "normal")
        zona = zona_para(p["variacao"], anterior)
        novo_estado[moeda] = zona
        resumo.append(f"{simbolo}: {pct_br(p['variacao'])} · {linha_preco(p)}")
        print(f"{simbolo:4} {pct_br(p['variacao']):>8}  zona={zona} (antes: {anterior})")

        if zona != anterior and zona in ("queda", "alta"):
            if zona == "queda":
                titulo = f"{nome} ({simbolo}) em queda: {pct_br(p['variacao'])} em 24h"
                tags = ["chart_with_downwards_trend"]
            else:
                titulo = f"{nome} ({simbolo}) em alta: {pct_br(p['variacao'])} em 24h"
                tags = ["chart_with_upwards_trend"]
            mensagem = f"Preço: {linha_preco(p)}\n{agora} (Brasília) · fonte: {fonte}"
            enviar_notificacao(titulo, mensagem, tags,
                               link=f"https://www.coingecko.com/en/coins/{moeda}")

    if MODO_TESTE:
        enviar_notificacao("Teste OK — monitor de cripto ativo",
                           "\n".join(resumo) + f"\n{agora} (Brasília) · fonte: {fonte}",
                           ["white_check_mark"], prioridade=3)

    if novo_estado != estado:
        salvar_estado(novo_estado)


if __name__ == "__main__":
    main()
  
