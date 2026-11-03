import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { PNG } from 'pngjs';

// Lê o RGB de um pixel do screenshot atual (para detectar estado da tela canvas).
export async function pixel(page, x, y) {
  const buf = await page.screenshot();
  const png = PNG.sync.read(buf);
  const i = (png.width * y + x) * 4;
  return [png.data[i], png.data[i + 1], png.data[i + 2]];
}
const near = (rgb, [r, g, b], tol = 30) =>
  Math.abs(rgb[0] - r) < tol && Math.abs(rgb[1] - g) < tol && Math.abs(rgb[2] - b) < tol;

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const ART = path.join(ROOT, 'artifacts');
export const DL = path.join(ROOT, 'downloads');

export function env() {
  const raw = fs.readFileSync(path.join(ROOT, '.env'), 'utf8');
  const e = {};
  for (const line of raw.split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m) e[m[1]] = m[2];
  }
  return e;
}

let step = 0;
export async function snap(page, name) {
  step++;
  const tag = String(step).padStart(2, '0') + '-' + name;
  await page.screenshot({ path: path.join(ART, tag + '.png') }).catch(() => {});
  console.log(`[snap] ${tag}`);
  return tag;
}

// Full login: Webswing HTML form + canvas login form. Leaves page on the main screen.
export async function login(E, { headless = process.env.HEADLESS !== '0', onSessionGrid = null } = {}) {
  const browser = await chromium.launch({ headless });
  const ctx = await browser.newContext({
    acceptDownloads: true,
    viewport: { width: 1600, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') console.log('  console.error>', m.text().slice(0, 160)); });

  // goto tolerante: 'networkidle' não serve (Webswing mantém conexões abertas)
  let loaded = false;
  for (let i = 0; i < 3 && !loaded; i++) {
    try {
      await page.goto(E.FOLHA_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForSelector('#nameInput', { timeout: 30000 });
      loaded = true;
    } catch (e) {
      console.log(`goto tentativa ${i + 1} falhou: ${String(e).slice(0, 100)}`);
      await page.waitForTimeout(4000);
    }
  }
  if (!loaded) return { browser, ctx, page, ok: false, state: 'no-load', reason: 'não carregou a tela de login inicial' };

  await page.fill('#nameInput', E.FOLHA_USER1);
  await page.fill('#passwordInput', E.FOLHA_PASS1);
  await page.click('#loginFormSubmitButton');

  // Wait for sistema de folha canvas login to render + "Efetuando rotinas de LOGIN..." to settle
  await page.waitForTimeout(45000);
  await snap(page, 'folha-login-screen');

  // TODO: às vezes aparece uma tela de SELEÇÃO DE BASE (vários blocos). É sempre a
  // PRIMEIRA. Mapear o clique quando essa tela for capturada
  // em screenshot.

  // Canvas text entry: clica o campo, limpa com Control+A/Delete e digita devagar.
  //
  // O Control+A/Delete também serve para "acordar" o campo — é ele que absorve a perda
  // do 1º caractere do Webswing, no lugar do espaço-guarda que havia aqui antes.
  //
  // O que NÃO fazer: terminar com Home+Delete (era assim que o espaço-guarda saía). O
  // clique no campo seguinte chega enquanto o canvas ainda processa essas teclas e é
  // engolido: o foco continua no Usuário e a senha é digitada por cima dele, o que o
  // sistema de folha acusa como "Usuário não cadastrado" (reproduzido em 21/09/2026).
  async function fill(x, y, text) {
    await page.mouse.click(x, y);
    await page.waitForTimeout(1200);
    await page.keyboard.press('Control+A');
    await page.waitForTimeout(250);
    await page.keyboard.press('Delete');
    await page.waitForTimeout(2000);
    await page.keyboard.type(text, { delay: 110 });
    await page.waitForTimeout(2000);
  }

  const isBright = (p) => p[0] > 200 && p[1] > 200 && p[2] > 200;
  const isDark = (p) => p[0] < 60 && p[1] < 60 && p[2] < 70;
  const isBlue = (p) => p[2] > 110 && p[2] - p[0] > 40 && p[2] - p[1] > 20;

  // Preenche usuário/senha e envia; repete se continuar na tela de login (azul).
  // Espera a tela de login estar de fato na frente. Sem isso, um diálogo ainda aberto
  // (o de sessões simultâneas fica ~20s e some sozinho) recebe a digitação no lugar dos
  // campos, e a tentativa seguinte se perde.
  async function esperarFormulario(timeoutMs = 30000) {
    const fim = Date.now() + timeoutMs;
    while (Date.now() < fim) {
      const p = await pixel(page, 800, 460);
      if (isBlue(p)) return true;
      await page.waitForTimeout(2000);
    }
    return false;
  }

  let state = 'unknown';
  let dialogos = 0;
  for (let attempt = 1; attempt <= 3 && state === 'unknown'; attempt++) {
    if (!(await esperarFormulario())) {
      await snap(page, `login-sem-formulario-${attempt}`);
      state = 'dialog';
      break;
    }
    await fill(700, 538, E.FOLHA_USER2);   // Usuário
    await fill(888, 538, E.FOLHA_PASS2);   // Senha
    await snap(page, `folha-login-filled-${attempt}`);
    await page.mouse.click(975, 532);           // seta / enviar
    await page.waitForTimeout(3000);

    // Detecta resultado por pixel:
    //  bright(800,460) -> diálogo "excedeu sessões"
    //  dark   -> tela principal (login OK)
    //  blue   -> ainda na tela de login (paste falhou) -> tenta de novo
    let mainStreak = 0;
    for (let i = 0; i < 16; i++) {
      await page.waitForTimeout(5000);
      const a = await pixel(page, 800, 460);
      const b = await pixel(page, 260, 300);
      if (isBright(a)) { state = 'dialog'; break; }
      if (isDark(a) && isDark(b)) { mainStreak++; } else { mainStreak = 0; }
      if (mainStreak >= 2) { state = 'main'; break; }
      if (i >= 1 && isBlue(a) && isBlue(b)) break;   // travou no login -> re-tenta
    }

    // "Usuário não cadastrado" / "senha não confere" por digitação perdida: fecha o aviso
    // e tenta de novo. Só até o limite do laço — repetir sem parar bloqueia o usuário.
    if (state === 'dialog' && attempt < 3 && ++dialogos < 2) {
      await snap(page, `login-dialogo-${attempt}`);
      await page.keyboard.press('Enter').catch(() => {});   // OK / Não é o botão em foco
      await page.waitForTimeout(2500);
      if (await esperarFormulario(20000)) state = 'unknown';  // voltou para a tela de login
    }
  }
  await snap(page, `login-result-${state}`);
  const reason = state === 'main' ? null
    : state === 'dialog'
      ? `Diálogo no login do sistema de folha. O mais comum é o limite de sessões: "Você excedeu a quantidade máxima de sessões simultâneas ... para o seu usuário (1)" — alguém está com o sistema de folha de "${E.FOLHA_USER2}" aberto, e só sai quando essa sessão for encerrada. Pode ser também "senha não confere", por digitação perdida no canvas. Ver artifacts/*login-dialogo-*.png e *login-result-dialog.png. NÃO repetir em loop (risco de bloquear o usuário).`
      : `Não consegui chegar à tela principal do sistema de folha — as credenciais não entraram no canvas, ou um diálogo ficou na frente. Ver artifacts/*login-result-unknown.png.`;
  // NUNCA fecha o browser aqui — o teardown() do script cuida disso (com logout best-effort),
  // inclusive nos casos de falha, para não deixar sessão pendurada.
  return { browser, ctx, page, ok: state === 'main', state, reason };
}

// Troca a empresa ativa. Botão ⇄ (canto sup. esq., ao lado de "Empresa:") abre a
// janela "Troca de empresa". Busca por sigla; se não resolver, tenta pela razão social.
// `alvo`: { sigla?, nome? }
export async function trocarEmpresa(page, alvo) {
  // A janela "Troca de empresa" tem uma faixa azul ("Troca da empresa em processamento")
  // em ~(700, 330). Enquanto a janela existe, esse pixel é azul forte.
  const dialogAberto = async () => {
    const p = await pixel(page, 700, 330);
    return p[2] > 140 && p[2] - p[0] > 55 && p[2] - p[1] > 25;
  };

  await page.mouse.click(25, 30);          // botão ⇄
  await page.waitForTimeout(3500);
  await snap(page, 'troca-empresa');
  if (!(await dialogAberto())) throw new Error('Janela "Troca de empresa" não abriu');

  async function tentar(campoY, valor, label) {
    await page.mouse.click(645, campoY);   // campo de texto (Sigla ~391 / Raz.Social ~413)
    await page.waitForTimeout(500);
    await page.keyboard.press('Control+A');
    await page.waitForTimeout(150);
    await page.evaluate((t) => navigator.clipboard.writeText(t), valor);
    await page.keyboard.press('Control+V');
    await page.waitForTimeout(1800);        // grade filtra p/ a linha correspondente
    await page.mouse.click(1023, 643);      // botão "OK" (NÃO usar Enter: vaza p/ a tela de fundo)
    await page.waitForTimeout(6000);
    const fechou = !(await dialogAberto());
    await snap(page, `troca-${label}`);
    if (fechou) {
      // empresa já ativa, diálogo informativo ou janela órfã (ex.: um tile em foco
      // que reagiu a um clique/tecla) — limpa a tela antes de seguir.
      for (let i = 0; i < 6; i++) { await page.keyboard.press('Escape').catch(() => {}); await page.waitForTimeout(300); }
      await page.waitForTimeout(1500);
      await snap(page, `troca-${label}-limpo`);
    }
    return fechou;                          // true = janela fechou = trocou
  }

  let ok = false;
  if (alvo.sigla) ok = await tentar(391, alvo.sigla, 'sigla');
  if (!ok && alvo.nome) {
    await page.mouse.click(542, 413);      // radio "Raz. Social"
    await page.waitForTimeout(400);
    ok = await tentar(413, alvo.nome, 'nome');
  }
  if (!ok) {
    await page.keyboard.press('Escape').catch(() => {});
    throw new Error(`Não consegui trocar para a empresa (sigla=${alvo.sigla || '-'}, nome=${alvo.nome || '-'})`);
  }
  await page.waitForTimeout(2000);
  await snap(page, 'empresa-trocada');
}

// --- detecção de estado "limpo" ---
// Duas condições encerram a sessão do sistema de folha no servidor (o que importa p/ o limite):
//   a) tela de login inicial (#nameInput)            -> tudo encerrado
//   b) "The application has been closed" (Webswing)   -> o sistema de folha fechou = sessão encerrada
async function atLoginInicial(page) {
  return (await page.locator('#nameInput').count().catch(() => 0)) > 0;
}
async function appClosed(page) {
  return (await page.getByText('The application has been closed', { exact: false }).count().catch(() => 0)) > 0;
}
async function sessaoEncerrada(page) {
  return (await atLoginInicial(page)) || (await appClosed(page));
}

// Logout com VERIFICAÇÃO. Retorna true só quando confirma que a sessão sistema de folha encerrou.
// Em camadas e com retentativas — a sessão pendurada quebra o fluxo do time.
export async function logout(page) {
  try {
    for (let attempt = 1; attempt <= 5; attempt++) {
      if (await sessaoEncerrada(page)) break;

      // fecha qualquer janela/menu/modal aberto
      for (let i = 0; i < 6; i++) { await page.keyboard.press('Escape').catch(() => {}); await page.waitForTimeout(350); }
      // algumas janelas (ex.: "Visualização de recibos processados") NÃO fecham com Escape:
      // tenta o botão "Sair" (~1176,731), o OK de um aviso de redimensionamento (~1037,562)
      // e o X da janela maximizada (~1224,194).
      if (attempt >= 2) {
        await page.mouse.click(1176, 731).catch(() => {});
        await page.waitForTimeout(600);
        await page.mouse.click(1037, 562).catch(() => {});
        await page.waitForTimeout(600);
        await page.mouse.click(1224, 194).catch(() => {});
        await page.waitForTimeout(600);
        for (let i = 0; i < 4; i++) { await page.keyboard.press('Escape').catch(() => {}); await page.waitForTimeout(300); }
      }
      await page.waitForTimeout(700);

      // sistema de folha logado: menu hamburguer -> "S-Sair" -> confirma (Sim)
      await page.mouse.click(25, 58).catch(() => {});
      await page.waitForTimeout(1400);
      await page.mouse.click(72, 117).catch(() => {});     // "S-Sair"
      await page.waitForTimeout(2500);
      await page.keyboard.press('Enter').catch(() => {});  // "Deseja realmente sair?" -> Sim
      await page.waitForTimeout(6000);
      await snap(page, `logout-try-${attempt}`);
    }

    // Webswing: sair da sessão de vez ("Sign out" / ícone do canvas)
    for (let i = 0; i < 4 && !(await atLoginInicial(page)); i++) {
      await page.getByText('Sign out', { exact: false }).click({ timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(2500);
      await page.mouse.click(1545, 878).catch(() => {});   // ícone logout no canto do canvas
      await page.waitForTimeout(1800);
    }
    await snap(page, 'logout-final');

    const encerrada = await sessaoEncerrada(page);
    console.log(encerrada
      ? `logout: OK — sessão sistema de folha encerrada (${(await atLoginInicial(page)) ? 'tela de login' : 'app fechado'}).`
      : 'logout: FALHOU — NÃO confirmei o encerramento; a sessão pode segurar ~10min.');
    return encerrada;
  } catch (e) {
    console.log('logout() erro:', String(e).slice(0, 140));
    return false;
  }
}

// Encerra a automação SEMPRE tentando logout ANTES de fechar o navegador,
// e só fecha depois de confirmar (ou esgotar as tentativas).
export async function teardown({ browser, page }) {
  let ok = false;
  for (let round = 1; round <= 2 && !ok; round++) ok = await logout(page);
  if (!ok) console.log('teardown: ATENÇÃO — fechando o navegador SEM confirmar o logout do sistema de folha.');
  await browser.close();
  return ok;
}
