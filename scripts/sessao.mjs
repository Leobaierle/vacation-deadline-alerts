// Sessão sistema de folha controlada por arquivo, para mapear as telas por screenshot numa só sessão.
// Faz login, depois executa as linhas anexadas em ctl/cmds.txt (use scripts/cmd.sh).
// Encerra SEMPRE com logout: comando "quit", SIGINT/SIGTERM ou IDLE_MIN sem comandos.
//
// Uso: DISPLAY=localhost:10.0 HEADLESS=0 node scripts/sessao.mjs
import fs from 'node:fs';
import path from 'node:path';
import { env, login, snap, teardown, trocarEmpresa, pixel, DL, ROOT } from './lib.mjs';

const CTL = path.join(ROOT, 'ctl');
const CMDS = path.join(CTL, 'cmds.txt');
const FEITOS = path.join(CTL, 'feitos.txt');
const IDLE_MS = Number(process.env.IDLE_MIN || 20) * 60000;
fs.mkdirSync(CTL, { recursive: true });
fs.mkdirSync(DL, { recursive: true });
fs.writeFileSync(CMDS, '');
fs.writeFileSync(FEITOS, '0');

const s = await login(env());
const { page } = s;
let fim = false;

async function encerrar(motivo) {
  if (fim) return;
  fim = true;
  console.log(`[sessao] encerrando (${motivo})`);
  await teardown(s);
  fs.writeFileSync(FEITOS, '999999');
  process.exit(0);
}
process.on('SIGINT', () => encerrar('SIGINT'));
process.on('SIGTERM', () => encerrar('SIGTERM'));

page.on('download', async (d) => {
  const local = path.join(DL, d.suggestedFilename());
  await d.saveAs(local);
  console.log(`[download] ${local}`);
});

async function exec(linha) {
  const [c, ...a] = linha.split(' ');
  const n = a.map(Number);
  console.log(`[cmd] ${linha}`);
  try {
    switch (c) {
      case 'click': await page.mouse.click(n[0], n[1]); break;
      case 'dbl': await page.mouse.dblclick(n[0], n[1]); break;
      case 'move': await page.mouse.move(n[0], n[1]); break;
      case 'key': await page.keyboard.press(a[0]); break;
      case 'type': await page.keyboard.type(a.join(' '), { delay: 110 }); break;
      case 'paste':
        await page.evaluate((t) => navigator.clipboard.writeText(t), a.join(' '));
        await page.keyboard.press('Control+V');
        break;
      case 'wait': await page.waitForTimeout(n[0]); break;
      case 'snap': await snap(page, a[0] || 'tela'); break;
      case 'pixel': console.log(`[pixel] ${n[0]},${n[1]} = ${await pixel(page, n[0], n[1])}`); break;
      case 'empresa': await trocarEmpresa(page, { sigla: a[0] }); break;
      case 'quit': await encerrar('quit'); break;
      default: console.log(`[cmd] desconhecido: ${c}`);
    }
  } catch (e) {
    console.log(`[erro] ${linha}: ${String(e).slice(0, 160)}`);
  }
}

if (!s.ok) {
  console.log('[sessao] login falhou:', s.reason);
  await encerrar('login');
}
await snap(page, 'main');
console.log('[sessao] pronto');

let feitos = 0;
let ultimo = Date.now();
while (!fim) {
  const raw = fs.readFileSync(CMDS, 'utf8');
  // só linhas completas: cmd.sh pode estar no meio de uma escrita
  const linhas = raw.slice(0, raw.lastIndexOf('\n') + 1).split('\n').filter((l) => l.trim());
  if (linhas.length > feitos) {
    for (const l of linhas.slice(feitos)) {
      feitos++;
      await exec(l.trim());
      if (fim) break;
    }
    ultimo = Date.now();
    fs.writeFileSync(FEITOS, String(feitos));
    console.log(`[sessao] ok ${feitos}`);
  } else if (Date.now() - ultimo > IDLE_MS) {
    await encerrar('ocioso');
  }
  await page.waitForTimeout(700);
}
