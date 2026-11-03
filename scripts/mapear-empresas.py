#!/usr/bin/env python3
"""Casa a lista de clientes do DP (planilha do portal, por razão social) com as empresas
do sistema de folha (sigla + CNPJ), e escreve o esqueleto de config/destinatarios.csv.

A planilha do DP traz nome comercial e Cliente ID do portal — não traz sigla nem CNPJ.
O nome no sistema de folha é a razão social, quase sempre diferente ("Alfa" x "ALFA HIDRAULICA
LTDA."), então o casamento é por aproximação e **o que ficar duvidoso sai marcado para
conferência humana**, nunca chutado para dentro do CSV.

Uso:
    python3 scripts/mapear-empresas.py ~/aviso-ferias-empresas.xlsx
    python3 scripts/mapear-empresas.py lista.xlsx --saida config/destinatarios.csv
"""

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
FERIAS_JSON = ROOT / "reports" / "ferias.json"

# Termos que só atrapalham a comparação: aparecem em quase toda razão social.
RUIDO = {
    "ltda", "sa", "s", "a", "me", "epp", "eireli", "cia", "e", "de", "da", "do", "das",
    "dos", "comercio", "com", "industria", "ind", "servicos", "serv", "ltd",
}


def normaliza(texto):
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", str(texto or "")) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]+", " ", sem_acento.casefold()).strip()


def tokens(texto):
    """Palavras que identificam a empresa. Se sobrar nada — "V & V Comércio" é só
    ruído e letra solta — devolve o nome inteiro, senão a comparação zera."""
    uteis = [t for t in normaliza(texto).split() if t not in RUIDO and len(t) > 1]
    return uteis or normaliza(texto).split()


def casa_palavra(a, b):
    """O sistema de folha trunca a razão social no cadastro: "XYZ IND E COM DE COMP ELETR SA" é
    "XYZ Industria e Comércio de Componentes Eletronicos". Por isso uma palavra casa
    com a outra quando qualquer uma começa pela outra, e não só na igualdade."""
    if a == b:
        return True
    menor, maior = (a, b) if len(a) <= len(b) else (b, a)
    return len(menor) >= 3 and maior.startswith(menor)


def semelhanca(nome_dp, nome_folha, sigla):
    """0..1. Combina cobertura de palavras (nos dois sentidos) com semelhança de string."""
    a, b = tokens(nome_dp), tokens(nome_folha)
    if not a or not b:
        return 0.0
    cobertura_dp = sum(1 for t in a if any(casa_palavra(t, o) for o in b)) / len(a)
    cobertura_folha = sum(1 for t in b if any(casa_palavra(t, o) for o in a)) / len(b)
    cobertura = (cobertura_dp + cobertura_folha) / 2
    razao = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    nota = 0.75 * cobertura + 0.25 * razao

    # O nome do portal costuma ser o começo da razão social ("QR Comercio" ->
    # "QR COMERCIO DE ALIMENTOS LTDA"). É sinal forte, e resgata os nomes que o filtro
    # de ruído esvazia ("V & V Comércio" vira só palavra vazia e letra solta).
    inteiro_dp, inteiro_folha = normaliza(nome_dp), normaliza(nome_folha)
    if len(inteiro_dp) >= 5 and (inteiro_folha.startswith(inteiro_dp) or inteiro_dp.startswith(inteiro_folha)):
        nota = max(nota, 0.85)
    # a sigla do sistema de folha costuma ser o começo do nome do DP (Alfa -> ALF)
    if a and normaliza(sigla) and a[0].startswith(normaliza(sigla)):
        nota = min(1.0, nota + 0.1)
    return nota


def empresas_da_folha():
    if not FERIAS_JSON.exists():
        raise SystemExit(f"Faltando {FERIAS_JSON.relative_to(ROOT)} — rodar os passos 1 e 2 antes")
    registros = json.loads(FERIAS_JSON.read_text(encoding="utf-8"))
    empresas = {}
    for reg in registros:
        chave = (reg.get("empresa") or "").strip().upper()
        if not chave:
            continue
        item = empresas.setdefault(chave, {
            "sigla": chave, "nome": reg.get("empresa_nome", ""), "cnpjs": set(), "periodos": 0,
        })
        item["cnpjs"].add(reg.get("cnpj", ""))
        item["periodos"] += 1
    return list(empresas.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("planilha", help="XLSX do DP (colunas: Cliente ID, Empresa, Ativo)")
    ap.add_argument("--coluna", type=int, default=2, help="coluna do nome da empresa (padrão: 2)")
    ap.add_argument("--saida", default=None, help="grava o esqueleto do destinatarios.csv")
    ap.add_argument("--limiar", type=float, default=0.72, help="nota mínima para casar sem conferência")
    args = ap.parse_args()

    ws = openpyxl.load_workbook(args.planilha, data_only=True).worksheets[0]
    clientes = [
        (str(ws.cell(row=l, column=1).value or "").strip(), str(ws.cell(row=l, column=args.coluna).value or "").strip())
        for l in range(2, ws.max_row + 1)
    ]
    clientes = [(cid, nome) for cid, nome in clientes if nome]
    folha = empresas_da_folha()

    # Atribuição 1-para-1, do par mais forte para o mais fraco: uma empresa do sistema de folha não
    # pode servir a dois clientes. É o que separa "QR Comercio" de "QR Parking" — sem
    # isso os dois disputam a mesma sigla e o desempate vira sorte.
    pares = sorted(
        ((semelhanca(nome, e["nome"], e["sigla"]), cid, nome, e) for cid, nome in clientes for e in folha),
        key=lambda x: -x[0],
    )
    escolha, empresa_usada = {}, set()
    for nota, cid, nome, e in pares:
        if (cid, nome) in escolha or e["sigla"] in empresa_usada:
            continue
        escolha[(cid, nome)] = (nota, e)
        empresa_usada.add(e["sigla"])

    casados, ressalva, sem_par = [], [], []
    for cid, nome in clientes:
        nota, empresa = escolha.get((cid, nome), (0.0, None))
        if empresa is None or nota < 0.40:
            sem_par.append((cid, nome))
        elif nota >= args.limiar:
            casados.append((cid, nome, empresa, nota))
        else:
            ressalva.append((cid, nome, empresa, nota))

    print(f"{len(clientes)} cliente(s) na lista x {len(folha)} empresa(s) com férias no sistema de folha\n")
    print(f"CASADOS ({len(casados)}):")
    for cid, nome, e, nota in casados:
        print(f"  [{nota:.2f}] {nome}  ->  {e['sigla']}  {e['nome']}  ({e['periodos']} período(s))")

    if ressalva:
        print(f"\nCASADOS COM RESSALVA ({len(ressalva)}) — entram no CSV, mas CONFIRA:")
        for cid, nome, e, nota in ressalva:
            print(f"  [{nota:.2f}] {nome}  ->  {e['sigla']}  {e['nome']}  ({e['periodos']} período(s))")

    if sem_par:
        print(f"\nSEM PAR NO sistema de folha ({len(sem_par)}) — ou não têm ninguém na listagem de férias,")
        print("  ou a razão social é bem diferente do nome do portal:")
        for cid, nome in sem_par:
            print(f"  - {nome} (Cliente ID {cid})")

    if args.saida:
        destino = Path(args.saida)
        linhas = [
            "# A lista de clientes do aviso de férias.",
            "#",
            "# O passo 1 emite a listagem para a base inteira do sistema de folha; é ESTE arquivo que define",
            "# quem recebe. Empresa que não estiver aqui fica de fora — não vira pendência.",
            "#",
            "# Formato:  <sigla ou cnpj>;<nome da empresa>;<e-mails separados por vírgula>",
            "#   - a 1ª coluna aceita a sigla do sistema de folha (ex.: ABC) ou o CNPJ;",
            "#   - pela sigla, as filiais do cliente (XYZ-1, XYZ-2, ...) saem num e-mail só;",
            "#   - o nome da empresa é o que aparece no assunto do e-mail;",
            "#   - ninguém entra em cópia.",
            "#",
            "# Gerado por scripts/mapear-empresas.py — FALTA PREENCHER OS E-MAILS.",
            "",
        ]
        for cid, nome, e, nota in sorted(casados + ressalva, key=lambda x: x[1].casefold()):
            if nota < args.limiar:
                linhas.append(f"# conferir o casamento abaixo: sistema de folha {e['sigla']} = {e['nome']}")
            linhas.append(f"{e['sigla']};{nome};")
        if sem_par:
            linhas += ["", "# Sem par na extração do sistema de folha — nenhum funcionário apareceu na listagem",
                       "# de prazo concessivo. Falta descobrir a sigla para incluir:"]
            for cid, nome in sem_par:
                linhas.append(f"# {nome} (Cliente ID {cid})")
        destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        print(f"\nEsqueleto gravado em {destino} — {len(casados)} linha(s) prontas, faltando os e-mails.")


if __name__ == "__main__":
    main()
