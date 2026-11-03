#!/usr/bin/env python3
"""Passo 2: lê as planilhas XLSX emitidas pelo sistema de folha ("Listagem de
Prazo Concessivo de Férias", tipo de impressão X-Exporta como planilha XLSX) e grava
reports/ferias.json no contrato do passo 3.

Layout da planilha (uma aba por empresa, nome da aba = SIGLA-filial):

    1  * ---- * R E L A C A O   D E   F E R I A S   E   P R A Z O   D E   C O N C E S S A O * ----
    3  Empresa:<razão social>      CNPJ:<cnpj> IE:<ie>      Data da Empresa:<dd/mm/aaaa>
    6  Código | F.Reg | Nome do Funcionário | Período Aquisitivo | Dias | Faltas |
       Prazo orig. | Dias prorrog | Dias Tot Afast | Ult.data p/conce. | Dias pagos
    7  Departamento:  38 - ABC - MATRIZ
    8  295.0 | | WILLIAM ... | 01/01/2026 a 31/12/2026 | -7.5 | | 02/12/2027 | | | 02/12/2027 | 30.0

Regras de leitura:
  - Quando o funcionário tem mais de um período aquisitivo, as linhas seguintes vêm com
    Código e Nome em branco: valem o funcionário da última linha preenchida.
  - O vencimento é a coluna **Ult.data p/conce.**, não "Prazo orig.": ela já considera
    a prorrogação por afastamento (colunas "Dias prorrog" / "Dias Tot Afast").
    A data nunca é calculada aqui — sai pronta do sistema de folha.
  - "Dias" é o saldo de dias a gozar no período; <= 0 significa período já quitado.

Uso:
    python3 scripts/02-extrair.py                  # lê tudo que está em downloads/
    python3 scripts/02-extrair.py a.xlsx b.xlsx    # só esses arquivos
    python3 scripts/02-extrair.py --velho-ok       # aceita planilha com mais de 7 dias
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

# O XLSX do sistema de folha declara a dimensão da planilha errada: com read_only=True o openpyxl
# para na 1ª linha. Por isso o workbook é aberto no modo normal.
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS = ROOT / "downloads"
REPORTS = ROOT / "reports"
SAIDA = REPORTS / "ferias.json"

RE_EMPRESA = re.compile(r"Empresa:\s*(.+?)\s{2,}CNPJ:\s*([\d./-]+)")
RE_PERIODO = re.compile(r"(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")
RE_DEPTO = re.compile(r"Departamento:\s*(.+)")


def so_digitos(valor):
    return "".join(c for c in (valor or "") if c.isdigit())


def texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%d/%m/%Y")
    return str(valor).strip()


def iso(valor):
    """dd/mm/aaaa (ou datetime da planilha) -> aaaa-mm-dd."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    t = str(valor).strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, formato).date().isoformat()
        except ValueError:
            continue
    return None


def numero(valor):
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(str(valor).strip().replace(".", "").replace(",", "."))
    except ValueError:
        return None


def matricula(valor):
    """295.0 (a planilha traz o código como número) -> "295"."""
    n = numero(valor)
    return str(int(n)) if n is not None else ""


def ler_aba(ws):
    registros = []
    empresa_nome, cnpj = "", ""
    sigla = ws.title.split("-")[0].strip()
    departamento = ""
    atual = {"matricula": "", "funcionario": ""}

    for linha in ws.iter_rows(values_only=True):
        celulas = list(linha) + [None] * (11 - len(linha))
        primeira = texto(celulas[0])

        if "Empresa:" in primeira:
            m = RE_EMPRESA.search(primeira)
            if m:
                empresa_nome, cnpj = m.group(1).strip(), so_digitos(m.group(2))
            continue

        m = RE_DEPTO.match(primeira)
        if m:
            departamento = m.group(1).strip()
            continue

        # cabeçalhos, totais e linhas decorativas
        if not any(texto(c) for c in celulas):
            continue
        if "**" in primeira or "**" in texto(celulas[2]):
            continue
        if primeira.startswith("*") or primeira.startswith("Usuario:") or primeira.startswith("Ordem:"):
            continue
        if primeira == "Código":
            continue

        periodo = RE_PERIODO.search(texto(celulas[3]))
        if not periodo:
            continue   # linha sem período aquisitivo não é registro de férias

        cod = matricula(celulas[0])
        nome = texto(celulas[2])
        if cod:
            atual = {"matricula": cod, "funcionario": nome}
        elif not atual["matricula"]:
            continue   # continuação sem dono: planilha fora do formato esperado

        registros.append({
            "empresa": sigla,
            "empresa_nome": empresa_nome,
            "cnpj": cnpj,
            "departamento": departamento,
            "matricula": atual["matricula"],
            "funcionario": atual["funcionario"],
            "periodo_aquisitivo_inicio": iso(periodo.group(1)),
            "periodo_aquisitivo_fim": iso(periodo.group(2)),
            "fim_periodo_concessivo": iso(celulas[9]),
            "prazo_concessivo_original": iso(celulas[6]),
            "dias_saldo": numero(celulas[4]) or 0.0,
            "dias_pagos": numero(celulas[10]) or 0.0,
            "faltas": numero(celulas[5]) or 0.0,
        })
    return registros


def main():
    args = [a for a in sys.argv[1:] if a != "--velho-ok"]
    aceita_velho = "--velho-ok" in sys.argv[1:]
    arquivos = [Path(a) for a in args] or sorted(DOWNLOADS.glob("*.XLSX")) + sorted(DOWNLOADS.glob("*.xlsx"))
    if not arquivos:
        raise SystemExit(f"Nenhuma planilha em {DOWNLOADS.relative_to(ROOT)} — rodar o passo 1 antes")

    # Se o passo 1 falhou, downloads/ continua com a planilha do mês passado e o passo 3
    # avisaria o cliente com dado velho, sem nada denunciar. Melhor parar.
    from datetime import datetime as _dt
    mais_nova = max(f.stat().st_mtime for f in arquivos)
    idade = (_dt.now() - _dt.fromtimestamp(mais_nova)).days
    print(f"Planilha mais recente: {_dt.fromtimestamp(mais_nova):%d/%m/%Y %H:%M} ({idade} dia(s))")
    if idade > 7 and not aceita_velho:
        raise SystemExit(
            f"A planilha mais nova de {DOWNLOADS.relative_to(ROOT)} tem {idade} dias — o passo 1 "
            "provavelmente não rodou. Rode-o, ou passe --velho-ok se a data for intencional."
        )

    registros, sem_data = [], []
    for arquivo in arquivos:
        wb = openpyxl.load_workbook(arquivo, data_only=True)
        for nome_aba in wb.sheetnames:
            lidos = ler_aba(wb[nome_aba])
            registros += lidos
            print(f"{arquivo.name} [{nome_aba}]: {len(lidos)} período(s)")
        wb.close()

    for reg in registros:
        if not reg["fim_periodo_concessivo"]:
            sem_data.append(f"{reg['empresa']}/{reg['matricula']} {reg['funcionario']}")

    REPORTS.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")
    empresas = {r["cnpj"] for r in registros}
    print(f"\n{len(registros)} período(s) de {len(empresas)} empresa(s) -> {SAIDA.relative_to(ROOT)}")

    if sem_data:
        # A data tem que sair do sistema de folha; sem ela o passo 3 para. Falha de extração,
        # não algo para contornar no envio.
        print(f"\nATENÇÃO: {len(sem_data)} registro(s) sem 'Ult.data p/conce.':")
        for item in sem_data[:20]:
            print(f"  - {item}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
