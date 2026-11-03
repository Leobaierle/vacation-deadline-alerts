#!/usr/bin/env python3
"""Passo 3: filtra as férias cujo prazo concessivo vence em <= 30 dias (ou já venceu,
caso em que a dobra do art. 137 da CLT já incide), avisa cada cliente por e-mail
e manda a relação das empresas avisadas para o DP.

Entrada:  reports/ferias.json          (saída do passo 2)
          config/destinatarios.csv     (a lista de clientes do aviso)
Saída:    reports/enviados-AAAA-MM.json  (idempotência: não reenvia no mesmo mês)

O passo 1 emite a listagem para a base inteira do sistema de folha, então é o destinatarios.csv
que define o alcance do aviso: empresa que não está lá fica de fora de propósito e
não entra como pendência no resumo do DP.

Uso:
    python3 scripts/03-enviar-avisos.py --dry-run     # não envia, grava em outbox/
    python3 scripts/03-enviar-avisos.py               # envia de verdade
"""

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mailer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
FERIAS_JSON = REPORTS / "ferias.json"
DESTINATARIOS_CSV = ROOT / "config" / "destinatarios.csv"
TEMPLATE = ROOT / "templates" / "aviso.txt"


def so_digitos(valor):
    return "".join(c for c in (valor or "") if c.isdigit())


def normaliza_chave(valor):
    """A 1ª coluna do destinatarios.csv aceita a sigla do sistema de folha (ABC) ou o CNPJ."""
    bruto = (valor or "").strip()
    digitos = so_digitos(bruto)
    return digitos if len(digitos) >= 8 else bruto.upper()


def carregar_destinatarios():
    """Lê config/destinatarios.csv: <sigla ou cnpj>;<empresa>;<e-mails por vírgula>.

    Devolve as entradas na ordem do arquivo e o índice por chave. Uma entrada sem
    e-mail é mantida: aparece como pendência no resumo do DP, em vez de sumir.
    """
    if not DESTINATARIOS_CSV.exists():
        raise SystemExit(f"Faltando {DESTINATARIOS_CSV.relative_to(ROOT)}")

    entradas, por_chave = [], {}
    with DESTINATARIOS_CSV.open(encoding="utf-8") as fh:
        linhas = (l for l in fh if l.strip() and not l.lstrip().startswith("#"))
        for row in csv.reader(linhas, delimiter=";"):
            if len(row) < 2:
                continue
            chave = normaliza_chave(row[0])
            if not chave:
                continue
            emails = row[2] if len(row) > 2 else ""
            entrada = {
                "chave": chave,
                "empresa": row[1].strip() or chave,
                "emails": [e.strip() for e in emails.split(",") if e.strip()],
                "vista_na_folha": False,
            }
            entradas.append(entrada)
            por_chave[chave] = entrada

    if not entradas:
        raise SystemExit(
            f"{DESTINATARIOS_CSV.relative_to(ROOT)} está vazio — sem a lista de clientes "
            "não há a quem avisar."
        )
    return entradas, por_chave


def carregar_ferias():
    if not FERIAS_JSON.exists():
        raise SystemExit(f"Faltando {FERIAS_JSON.relative_to(ROOT)} — rodar o passo 2 antes")
    return json.loads(FERIAS_JSON.read_text(encoding="utf-8"))


def entrada_do_registro(reg, por_chave):
    """Liga um registro do sistema de folha à linha do destinatarios.csv, por CNPJ ou por sigla.

    Pela sigla, as filiais de um mesmo cliente (XYZ-1, XYZ-2, ...) caem na mesma linha
    e saem num e-mail só; pelo CNPJ, cada filial é tratada à parte.
    """
    for candidata in (so_digitos(reg.get("cnpj")), (reg.get("empresa") or "").strip().upper()):
        if candidata and candidata in por_chave:
            return por_chave[candidata]
    return None


def data_limite_concessivo(reg):
    """Data em que as férias passam a ser devidas em dobro.

    É o fim do **período concessivo**, e não o fim do aquisitivo, que apenas dá
    origem ao direito. Passada essa data incide a dobra do art. 137 da CLT.

    O valor vem sempre da coluna "Ult.data p/conce." da planilha do sistema de folha (passo 2) —
    nunca calculado aqui. Se faltar, é falha da extração e o lote para.
    """
    pronta = reg.get("fim_periodo_concessivo")
    if not pronta:
        raise SystemExit(
            f"Registro sem 'fim_periodo_concessivo' (empresa {reg.get('empresa')}, "
            f"matrícula {reg.get('matricula')}) — a data tem que sair do sistema de folha; "
            "revisar o passo 2 antes de enviar."
        )
    return datetime.strptime(pronta, "%Y-%m-%d").date()


def a_vencer(registros, limite, por_chave):
    """Períodos com saldo pendente, de cliente da lista, cujo concessivo vence até `limite`.

    Quem já passou da data continua entrando: a dobra do art. 137 não faz a pendência
    desaparecer.
    """
    selecionados = []
    for reg in registros:
        entrada = entrada_do_registro(reg, por_chave)
        if entrada is None:
            continue                       # empresa fora da lista de clientes
        entrada["vista_na_folha"] = True
        if float(reg.get("dias_saldo") or 0) <= 0:
            continue
        vencimento = data_limite_concessivo(reg)
        if vencimento <= limite:
            selecionados.append({**reg, "_vencimento": vencimento, "_entrada": entrada})
    return selecionados


def agrupar_por_cliente(registros):
    """Um e-mail por linha do destinatarios.csv."""
    grupos = {}
    for reg in registros:
        grupos.setdefault(reg["_entrada"]["chave"], []).append(reg)
    for itens in grupos.values():
        itens.sort(key=lambda r: (r["_vencimento"], r.get("funcionario", "")))
    return grupos


def chave_periodo(reg):
    """Identifica o período de férias de um funcionário dentro da empresa."""
    return f"{reg.get('empresa', '')}|{reg.get('matricula', '')}|{reg.get('periodo_aquisitivo_fim', '')}"


def br(data):
    return data.strftime("%d/%m/%Y")


def dias(valor):
    """O sistema de folha traz o saldo com meio dia (27,5). Inteiro sai sem casa decimal."""
    n = float(valor or 0)
    return (f"{n:.0f}" if n == int(n) else f"{n:.1f}").replace(".", ",")


def montar_tabela(itens):
    """Um bloco de campos rotulados por período — não uma tabela em colunas.

    Em texto puro, com a fonte proporcional do Outlook/webmail, coluna alinhada por
    espaço desalinha; rótulo por linha lê igual em qualquer cliente de e-mail.
    """
    blocos = []
    for r in itens:
        blocos.append(
            f"Colaborador: {r.get('funcionario', '')}\n"
            f"Data limite para gozo: {br(r['_vencimento'])}\n"
            f"Dias disponíveis: {dias(r.get('dias_saldo'))}"
        )
    return "\n\n".join(blocos)


def montar_blocos(itens, hoje, janela_dias):
    """Separa os já vencidos (férias em dobro) dos que ainda vão vencer."""
    vencidos = [r for r in itens if r["_vencimento"] < hoje]
    a_vencer_ainda = [r for r in itens if r["_vencimento"] >= hoje]

    blocos = []
    if vencidos:
        blocos.append(
            "EM SITUAÇÃO DE VENCIMENTO - prazo concessivo já vencido:\n\n"
            + montar_tabela(vencidos)
        )
    if a_vencer_ainda:
        blocos.append(
            f"A VENCER nos próximos {janela_dias} dias:\n\n" + montar_tabela(a_vencer_ainda)
        )
    return "\n\n".join(blocos)


def montar_email(empresa, itens, janela_dias, hoje):
    modelo = TEMPLATE.read_text(encoding="utf-8")
    corpo = modelo.format(
        empresa=empresa, janela_dias=janela_dias, tabela=montar_blocos(itens, hoje, janela_dias)
    )
    assunto, corpo = corpo.split("\n", 1)
    return assunto.removeprefix("Assunto:").strip(), corpo.lstrip("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="não envia; grava as mensagens em outbox/")
    ap.add_argument("--janela", type=int, help="dias de antecedência (padrão: JANELA_DIAS do .env, ou 30)")
    args = ap.parse_args()

    mailer.load_env()
    janela_dias = args.janela or int(os.getenv("JANELA_DIAS", "30"))
    hoje = date.today()
    limite = hoje + timedelta(days=janela_dias)

    entradas, por_chave = carregar_destinatarios()
    registros = carregar_ferias()
    grupos = agrupar_por_cliente(a_vencer(registros, limite, por_chave))
    print(f"{len(entradas)} cliente(s) na lista; {len(grupos)} com férias vencendo até {br(limite)}.")

    REPORTS.mkdir(exist_ok=True)
    registro = REPORTS / f"enviados-{hoje:%Y-%m}.json"
    enviados = json.loads(registro.read_text(encoding="utf-8")) if registro.exists() else {}

    resumo, pendencias = [], []
    for chave, itens in sorted(grupos.items()):
        entrada = por_chave[chave]
        if not entrada["emails"]:
            pendencias.append(f"{entrada['empresa']}: sem e-mail em destinatarios.csv")
            continue
        # Idempotência só dentro do mês: o registro é por competência, então no mês
        # seguinte a mesma pendência é avisada de novo — e assim todo mês, até as
        # férias serem gozadas e o funcionário sumir da listagem do sistema de folha.
        # Rodar duas vezes no mesmo mês não reenvia, a menos que tenha entrado
        # alguém novo na janela dos 30 dias.
        ja_avisados = set(enviados.get(chave, {}).get("periodos", []))
        periodos = [chave_periodo(r) for r in itens]
        novos = [p for p in periodos if p not in ja_avisados]
        if ja_avisados and not novos and not args.dry_run:
            print(f"[pula] {entrada['empresa']} — já avisado em {enviados[chave]['quando']}")
            continue

        assunto, corpo = montar_email(entrada["empresa"], itens, janela_dias, hoje)
        try:
            resultado = mailer.send(entrada["emails"], assunto, corpo, dry_run=args.dry_run)
        except Exception as erro:
            # Um cliente que falha não pode derrubar o lote: os outros ainda precisam
            # ser avisados, e o DP precisa saber quem ficou de fora.
            print(f"[ERRO] {entrada['empresa']} — {erro}")
            pendencias.append(f"{entrada['empresa']}: FALHOU o envio ({erro})")
            continue

        reenvio = " (novos na janela)" if ja_avisados else ""
        print(f"[ok] {entrada['empresa']} ({len(itens)} período(s)){reenvio} — {resultado}")

        if not args.dry_run:
            enviados[chave] = {
                "empresa": entrada["empresa"],
                "emails": entrada["emails"],
                "periodos": sorted(set(periodos) | ja_avisados),
                "quando": datetime.now().isoformat(timespec="seconds"),
            }
            # Grava a cada envio, não no fim: se o lote morrer no meio, quem já recebeu
            # fica registrado e não é avisado duas vezes na próxima tentativa.
            registro.write_text(json.dumps(enviados, ensure_ascii=False, indent=2), encoding="utf-8")
        resumo.append((entrada["empresa"], entrada["emails"], itens))

    # Cliente da lista que não apareceu em NENHUM registro da extração: pode ser uma
    # empresa sem ninguém na listagem (normal), mas também é assim que uma sigla ou um
    # CNPJ errado se manifesta — e aí o cliente nunca seria avisado.
    for entrada in entradas:
        if not entrada["vista_na_folha"]:
            pendencias.append(
                f"{entrada['empresa']} ({entrada['chave']}): nenhum registro na extração "
                "do sistema de folha — conferir a sigla/CNPJ em destinatarios.csv"
            )

    enviar_resumo_dp(resumo, pendencias, hoje, limite, janela_dias, args.dry_run)


def enviar_resumo_dp(resumo, pendencias, hoje, limite, janela_dias, dry_run):
    destino = os.getenv("MAIL_RESUMO", "dp@example.com")
    linhas = [
        f"Avisos de férias enviados em {br(hoje)} "
        f"(prazo concessivo vencendo até {br(limite)} — janela de {janela_dias} dias).",
        "",
    ]
    for empresa, emails, itens in resumo:
        vencidos = sum(1 for r in itens if r["_vencimento"] < hoje)
        funcionarios = len({r.get("matricula") for r in itens})
        detalhe = f"{funcionarios} funcionário(s), {len(itens)} período(s)"
        if vencidos:
            detalhe += f", sendo {vencidos} com prazo vencido"
        linhas.append(f"- {empresa}: {detalhe} -> {', '.join(emails)}")
    if not resumo:
        linhas.append("- nenhuma empresa avisada")
    if pendencias:
        linhas += ["", "Pendências:"] + [f"- {p}" for p in pendencias]

    resultado = mailer.send(
        [destino],
        f"Relação de avisos de férias enviados - {br(hoje)}",
        "\n".join(linhas) + "\n",
        dry_run=dry_run,
    )
    print(f"[resumo] {destino} — {resultado}")


if __name__ == "__main__":
    main()
