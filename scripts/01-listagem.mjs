// Passo 1: emite no sistema de folha a "Listagem de Prazo Concessivo de Férias"
// e salva a planilha XLSX em downloads/.
//
// Por padrão usa o "Múltiplas empresas" da própria tela: marca todas as empresas da
// base e o sistema de folha devolve UM arquivo com uma aba por empresa/filial (SIGLA-filial).
// São ~9 min para as ~300 empresas — contra uma troca de empresa e uma emissão para
// cada uma. Quem recebe aviso é decidido depois, no passo 3, por config/destinatarios.csv.
//
// Uso:
//   node scripts/01-listagem.mjs                 todas as empresas (padrão)
//   node scripts/01-listagem.mjs --empresa ABC   só uma empresa (teste rápido, ~1 min)
//   node scripts/01-listagem.mjs --data-ref 31/10/2026   muda a data de referência
//   HEADLESS=0 para acompanhar a tela.
import fs from 'node:fs';
import path from 'node:path';
import { env, login, snap, teardown, trocarEmpresa, pixel, DL } from './lib.mjs';

// Coordenadas mapeadas em 21/09/2026 (viewport 1600x900) — ver artifacts/ do mapeamento.
const UI = {
  buscaMenu: [100, 89],
  resultadoBusca: [135, 135],          // 1ª linha: "Listagem de Prazo Concessivo de Férias"
  dataReferencia: [798, 467],
  multiplasEmpresas: [999, 535],
  avancarParametros: [986, 588],
  avancarFuncionarios: [1180, 663],
  marcarTodasEmpresas: [311, 269],     // checkbox do cabeçalho da grade
  avancarEmpresas: [1247, 720],
  okDialogoGrupo: [952, 555],          // "É possível criar um grupo de empresas..."
  simConfirma: [709, 513],
  okSemRegistros: [952, 534],
  tipoXlsx: [568, 390],                // "X-Exporta como planilha XLSX"
  avancarImpressao: [1063, 698],
};

const args = process.argv.slice(2);
const opcao = (nome) => {
  const i = args.indexOf(nome);
  return i >= 0 ? args[i + 1] : null;
};
const SIGLA = opcao('--empresa');
const DATA_REF = opcao('--data-ref');
const MULTIPLAS = !SIGLA;

const clique = async (page, [x, y], espera = 2000) => {
  await page.mouse.click(x, y);
  await page.waitForTimeout(espera);
};

// A faixa azul do topo da janela "Configuração de Impressão" fica nesta altura;
// o aviso "Nenhum registro válido processado" é uma janela menor e mais abaixo.
async function janelaImpressao(page) {
  const p = await pixel(page, 800, 272);
  return p[2] > 140 && p[2] - p[0] > 55 && p[2] - p[1] > 25;
}

async function abrirListagem(page) {
  await clique(page, UI.buscaMenu, 1200);
  await page.keyboard.type('concessivo', { delay: 110 });
  await page.waitForTimeout(3000);
  await clique(page, UI.resultadoBusca, 7000);
  await snap(page, 'parametros');
}

async function main() {
  fs.mkdirSync(DL, { recursive: true });
  const sessao = await login(env());
  const { page } = sessao;
  if (!sessao.ok) {
    console.log('login falhou:', sessao.reason);
    await teardown(sessao);
    process.exit(1);
  }

  // O download pode sair a qualquer momento depois da confirmação; guarda a promessa antes.
  let arquivo = null;
  const baixado = new Promise((resolve) => {
    page.on('download', async (d) => {
      arquivo = path.join(DL, d.suggestedFilename());
      await d.saveAs(arquivo);
      console.log(`[download] ${arquivo}`);
      resolve(arquivo);
    });
  });

  try {
    if (SIGLA) await trocarEmpresa(page, { sigla: SIGLA });

    await abrirListagem(page);

    if (DATA_REF) {
      await clique(page, UI.dataReferencia, 500);
      await page.keyboard.press('Control+A');
      await page.keyboard.type(DATA_REF, { delay: 110 });
      await page.waitForTimeout(800);
    }
    // Demais parâmetros ficam no padrão do sistema de folha: agrupado por departamento, ordem por
    // código, "Incluir períodos a vencer" marcado (é o que traz os períodos em aberto).
    if (MULTIPLAS) await clique(page, UI.multiplasEmpresas, 2500);
    await snap(page, 'parametros-ok');

    await clique(page, UI.avancarParametros, 6000);      // -> seleção de funcionários
    await clique(page, UI.avancarFuncionarios, 8000);    // padrão = todos

    if (MULTIPLAS) {
      await snap(page, 'selecao-empresas');
      await clique(page, UI.marcarTodasEmpresas, 4000);
      await clique(page, UI.avancarEmpresas, 6000);
      await clique(page, UI.okDialogoGrupo, 2500);       // aviso sobre criar grupo
      await clique(page, UI.avancarEmpresas, 10000);
    }

    await snap(page, 'confirma');
    await clique(page, UI.simConfirma, 20000);

    if (!(await janelaImpressao(page))) {
      // empresa sem ninguém na listagem: sai um aviso "Nenhum registro válido processado"
      await snap(page, 'sem-registros');
      await clique(page, UI.okSemRegistros, 3000);
      console.log('sistema de folha não gerou listagem (nenhum registro válido para a seleção).');
      return;
    }

    await clique(page, UI.tipoXlsx, 2000);               // X-Exporta como planilha XLSX
    await snap(page, 'impressao-xlsx');
    await clique(page, UI.avancarImpressao, 5000);

    // Com múltiplas empresas o sistema de folha abre "Processamento de Múltiplas Empresas" e
    // percorre a base inteira antes de entregar o arquivo (~9 min para ~300 empresas).
    const limite = MULTIPLAS ? 30 * 60000 : 3 * 60000;
    const alvo = await Promise.race([
      baixado,
      new Promise((r) => setTimeout(() => r(null), limite)),
    ]);
    await snap(page, 'fim');

    if (!alvo) throw new Error(`Nenhum arquivo baixado em ${limite / 60000} min — ver artifacts/`);
    console.log(`\nPlanilha salva: ${alvo}\nPróximo passo: python3 scripts/02-extrair.py`);
  } finally {
    await teardown(sessao);
  }
}

await main();
