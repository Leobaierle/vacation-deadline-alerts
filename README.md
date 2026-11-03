# Aviso de férias a vencer

Gera a **listagem de prazo concessivo de férias** dos funcionários de todos os clientes num sistema de folha sem API
e manda um **alerta por e-mail** a cada cliente com férias vencidas ou perto de vencer. Depois manda um resumo ao
Departamento Pessoal.

> Projeto real publicado aqui sem dados de clientes. A lista de destinatários, as siglas e os e-mails são fictícios.

## Fluxo

1. **Listagem** (`scripts/01-listagem.mjs`): Playwright entra no sistema de folha, que é uma aplicação Java servida
   no navegador via **Webswing** (tudo num `<canvas>`, sem DOM). Ele acha o relatório pelo campo de pesquisa de
   menus, marca **"Múltiplas empresas"** e exporta como **XLSX**. Sai **um arquivo** com uma aba por empresa e
   filial, cerca de 300 empresas em ~9 min.
2. **Extração** (`scripts/02-extrair.py`): lê as abas para `reports/ferias.json` (contrato abaixo).
3. **Filtro e envio** (`scripts/03-enviar-avisos.py`): mantém os períodos com saldo pendente cujo prazo concessivo
   vence em até `JANELA_DIAS` (30) ou já venceu. Manda um e-mail por cliente e, no fim, o resumo para o DP.

## Regras que importam

- **Que data é o "vencimento":** o fim do **período concessivo**, os 12 meses depois do aquisitivo (CLT art. 134).
  Passou dela, as férias são devidas **em dobro** (art. 137). A data sai pronta do relatório e **nunca é calculada**
  pela automação. A coluna usada já considera a prorrogação por afastamento: um funcionário com anos de afastamento
  seria avisado indevidamente se o cálculo usasse o prazo original.
- **Vencidos em bloco separado** no e-mail ("férias devidas em dobro"). Eles continuam saindo todo mês até as
  férias serem gozadas.
- **Quem recebe é decidido pela lista**, não pelo código: `config/destinatarios.csv` aceita sigla (filiais num e-mail
  só) ou CNPJ (cada filial à parte). Cliente da lista que não aparece em nenhum registro vira **pendência no
  resumo**. É assim que uma sigla ou CNPJ errado aparece, em vez de o cliente simplesmente nunca ser avisado.
- **Reenvio idempotente por competência** (`reports/enviados-AAAA-MM.json`): rodar de novo no mesmo mês não reenvia,
  a não ser que alguém novo tenha entrado na janela. Nesse caso o cliente recebe o e-mail atualizado com a lista
  completa.
- **XLSX em vez de PDF:** já vem em colunas, sem extrair texto de PDF. Detalhe: a planilha declara a dimensão
  errada, e o `openpyxl` em `read_only=True` para na 1ª linha. Por isso o workbook é aberto no modo normal.
- **Texto puro legível:** cada período vira um bloco de campos rotulados (Colaborador / Data limite / Dias), que lê
  igual em qualquer cliente de e-mail. Respostas vão para o DP via `Reply-To`.

## Montando a lista de clientes

`scripts/mapear-empresas.py` casa a planilha de clientes do DP (nome comercial) com as empresas do sistema de folha
(razão social truncada, sigla, CNPJ). Usa tokens sem ruído ("ltda", "comércio"...), cobertura de palavras nos dois
sentidos, prefixo e a sigla como começo do nome, com atribuição 1-para-1 do par mais forte para o mais fraco. **O que
fica duvidoso sai marcado para conferência humana**, nunca vai direto para o CSV.

## Contrato `reports/ferias.json`

```json
[
  {
    "empresa": "ABC",
    "empresa_nome": "EMPRESA ABC LTDA",
    "cnpj": "11222333000181",
    "departamento": "38 - ABC - MATRIZ",
    "matricula": "1234",
    "funcionario": "Fulano de Tal",
    "periodo_aquisitivo_inicio": "2024-05-01",
    "periodo_aquisitivo_fim": "2025-04-30",
    "fim_periodo_concessivo": "2026-04-30",
    "prazo_concessivo_original": "2026-04-30",
    "dias_saldo": 30.0,
    "dias_pagos": 0.0,
    "faltas": 0.0
  }
]
```

`dias_saldo` é fracionário (27,5 é comum) e vem negativo para período já quitado. O passo 3 só depende desse
arquivo, então pode ser desenvolvido e testado com `--dry-run` sem o sistema de folha.

## Estrutura

```
config/destinatarios.csv     e-mails por empresa (sigla ou cnpj;empresa;emails)
scripts/lib.mjs              login em 2 etapas, troca de empresa e logout verificado (limite de 1 sessão)
scripts/01-listagem.mjs      emite a listagem e baixa o XLSX
scripts/02-extrair.py        XLSX -> reports/ferias.json
scripts/03-enviar-avisos.py  filtro da janela + envio + resumo
scripts/mapear-empresas.py   casamento aproximado lista do DP x empresas do sistema
scripts/sessao.mjs + cmd.sh  sessão dirigida por arquivo, para mapear telas por screenshot
scripts/mailer.py            SMTP (Office 365) com --dry-run gravando em outbox/
templates/aviso.txt          modelo do e-mail
```

## Rodando

```bash
npm install && npx playwright install chromium
cp .env.example .env

node scripts/01-listagem.mjs --empresa ABC      # teste com uma empresa
node scripts/01-listagem.mjs                    # base inteira
python3 scripts/02-extrair.py
python3 scripts/03-enviar-avisos.py --dry-run   # não envia, grava em outbox/
```

Stack: Node 22 + Playwright, Python 3.12 + openpyxl, SMTP.
