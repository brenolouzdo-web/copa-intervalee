# Copa Inter Vale Master — plataforma em tempo real

Site público (PWA, mobile first) + painel administrativo + **mesa do operador** para lançar gols, cartões e lances pelo celular.
Tudo que o operador lança aparece **na hora** na tela de quem está com o site aberto, e chega como **notificação** para quem seguiu a seleção — até com o site fechado.

**Stack:** Python 3.10+ · Flask · SQLite · Server-Sent Events (tempo real) · Web Push (VAPID) · HTML/CSS/JS puro (sem build).

---

## 1. Rodar no seu computador (VS Code)

```bash
cd copa-intervale
python -m venv venv
venv\Scripts\activate            # Windows      (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt

python seed.py                   # dados de EXEMPLO (fictícios) para você testar
ADMIN_PASSWORD=admin123 python app.py     # Windows (PowerShell):  $env:ADMIN_PASSWORD="admin123"; python app.py
```

Abra **http://localhost:5000** (site) e **http://localhost:5000/admin** (painel — usuário `admin`, senha a que você definiu;
se não definir, uma senha aleatória aparece no terminal na primeira execução).

Para começar **valendo**, sem dados de exemplo:

```bash
python seed.py --selecoes   # cadastra as 29 seleções confirmadas que aparecem no Instagram (confira no painel!)
python seed.py --limpar     # ou: zera tudo e cadastre do zero
```

Testes automáticos: `python -m unittest discover -s tests -v`

## 2. O que tem (checklist do pedido)

| Pedido | Onde |
|---|---|
| Início ao vivo (placar, minuto, escudos, estádio, transmissão, próximos, últimos, avisos) | `/` |
| Jogos e resultados filtráveis (fase, data, seleção, região, situação) | `/jogos` |
| Classificação por grupos automática (P, J, V, E, D, GP, GC, SG, cartões) + mata-mata | `/classificacao` |
| Seleções com escudo, cidade, região, elenco, técnico, histórico, jogos | `/selecoes`, `/selecao/<id>` |
| Artilharia, melhor ataque/defesa, disciplina | `/artilharia` |
| Súmula e narração em linha do tempo, árbitros, vídeo da transmissão | `/jogo/<id>` |
| Notícias, fotos, vídeos do YouTube e posts do Instagram | `/noticias` |
| Patrocinadores, regulamento, estádios, ingressos, contato | rodapé, `/patrocinadores`, `/info` |
| Indicador **AO VIVO** pulsante, placar animado no gol, relógio, contagem regressiva | todas |
| Seguir seleção + alertas (na tela e **push com o site fechado**) | sino 🔔 e botão “Seguir” |
| PWA instalável, offline básico, Open Graph, `sitemap.xml`, `robots.txt` | automático |
| Painel com login e 3 perfis (administrador, operador de jogo, editor) | `/admin` |

## 3. Como usar no dia de jogo

1. **Painel › Jogos**: cadastre o jogo (seleções, data, estádio, árbitros, link da transmissão).
2. No celular do operador: **Painel › Mesa** › escolha o jogo.
3. Toque **Iniciar jogo** → **GOL** (escolha o time e o atleta, ou “sem identificar”) → **Amarelo/Vermelho/Substituição/Gol contra** → **Narrar um lance** (texto livre) → **Intervalo / 2º tempo / Encerrar**.
4. Lançou errado? Toque no 🗑 do lançamento: o placar é recalculado.
5. Mata-mata (quartas com 1º e 2º dos grupos A, B, C e D): com a fase de grupos encerrada, **Painel › Jogos › Gerar quartas** e depois **Gerar próxima fase** (semi → final e 3º lugar). Empate no mata-mata: lance os pênaltis na mesa.
6. Tabela, artilharia e chaveamento se atualizam sozinhos.

**Perfis:** *Administrador* (tudo) · *Operador de jogo* (só a mesa) · *Editor* (notícias, fotos, patrocinadores). Crie em **Painel › Usuários**.
**Configurações** (nome, edição, aviso no topo, link da transmissão geral, duração do tempo, regulamento, ingressos, contatos): **Painel › Configurações**.

## 4. Publicar na internet

> **HTTPS é obrigatório** para PWA e notificações push (todas as opções abaixo dão HTTPS grátis).
> **O banco é um arquivo SQLite: hospede onde exista disco que não apaga** (VPS, Docker com volume, Fly/Railway/Render com *disk*). Em planos gratuitos com disco temporário os dados SOMEM a cada deploy.

### Opção A — VPS (Ubuntu) com nginx  ✅ recomendada

```bash
sudo apt install python3-venv nginx certbot python3-certbot-nginx
sudo mkdir -p /srv/copa-intervale && sudo chown $USER /srv/copa-intervale
# copie o projeto para /srv/copa-intervale e:
cd /srv/copa-intervale && python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env              # defina SECRET_KEY, ADMIN_PASSWORD, SITE_URL...
sudo cp deploy-systemd.service /etc/systemd/system/copa.service
sudo chown -R www-data /srv/copa-intervale
sudo systemctl enable --now copa
sudo cp deploy-nginx.conf /etc/nginx/sites-available/copaintervale   # ajuste o domínio
sudo ln -s /etc/nginx/sites-available/copaintervale /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d www.seudominio.com.br
```
O `deploy-nginx.conf` já desliga o *buffer* do `/stream` (sem isso o tempo real trava).

### Opção B — Docker
```bash
cp .env.example .env && nano .env
docker compose up -d --build
```
Banco, chaves e uploads ficam no volume `dados`. Coloque Caddy/nginx/Cloudflare Tunnel na frente para o HTTPS.

### Opção C — Railway (passo a passo)
1. Crie uma conta em **github.com** e um repositório **privado** novo; envie o conteúdo da pasta `copa-intervale` (no site do GitHub: *Add file › Upload files*, arrastando os arquivos já extraídos do zip).
2. Em **railway.com**, entre com o GitHub e assine o plano **Hobby** (cerca de US$ 5/mês). Clique em **New Project › Deploy from GitHub repo** e escolha o repositório. A Railway detecta o `Dockerfile` sozinha.
3. No serviço criado, aba **Variables**, adicione (veja `.env.example`):
   `SECRET_KEY` (texto longo e aleatório), `ADMIN_USER` (ex.: admin), `ADMIN_PASSWORD` (senha forte), `SITE_URL` (o endereço final do site).
   `PRODUCTION`, `TRUST_PROXY`, `DATABASE`, `UPLOADS_DIR` e `INSTANCE_DIR` já vêm preenchidos no `Dockerfile`.
4. Ainda no serviço, **Settings › Volumes (ou clique direito no serviço › Attach Volume)**: crie um volume com *mount path* **`/dados`**. É ele que guarda banco, chaves e fotos — sem o volume, os dados somem a cada atualização.
5. Aba **Settings › Networking › Generate Domain**: a Railway cria um endereço `https://….up.railway.app`. Abra `/admin` nele e entre com o usuário e a senha do passo 3.
6. Domínio próprio (opcional): em *Networking › Custom Domain* e aponte o DNS como a Railway indicar.
7. Para ver os dados de exemplo antes de usar de verdade: no terminal da Railway (*Settings › … › Shell*) rode `python seed.py`; para começar valendo, `python seed.py --selecoes`.

Se algo não subir, abra a aba **Deployments › View logs**: o erro aparece lá.

### Opção D — Render / Fly.io
Mesma ideia: use o `Dockerfile`, as mesmas variáveis e um **disco persistente montado em `/dados`** (no Render, só em instância paga).

### Capacidade
`gunicorn.conf.py` usa 2 processos × 300 threads (~600 torcedores conectados ao mesmo tempo; cada um ocupa 1 thread). Para milhares: `pip install gevent` e `WORKER_CLASS=gevent`.
Testado: 150 conexões simultâneas receberam um lance em ~0,3 s (`scripts/teste_carga_sse.py`). Dentro de cada processo, **uma única** consulta por segundo abastece todos os torcedores.

### Backup (importante!)
Copie `copa.db`, `vapid_private.pem` (chave do push — se mudar, os celulares precisam reativar os alertas) e a pasta `uploads/` (em Docker/Railway ficam todos em `/dados`; fora do Docker, em `instance/` e `static/uploads/`). Ex.: `cron` diário com `sqlite3 copa.db ".backup copa-AAAAMMDD.db"`.

## 5. Notificações push
- Android/Chrome/Edge/Firefox/desktop: tocar em 🔔 › **Ativar notificações**; depois “Seguir” nas seleções (ou “Avisar de todos os jogos”).
- **iPhone:** só funciona depois de instalar (Safari › Compartilhar › *Adicionar à Tela de Início*) — regra da Apple.
- Disparam em: início, gol, gol contra, intervalo, 2º tempo, fim de jogo e expulsão.
- A criptografia (RFC 8291) e o VAPID (RFC 8292) são implementados em `push.py` só com a biblioteca `cryptography`, e validados no teste automático contra o vetor oficial da RFC.

## 6. Personalizar
- **Logo/escudo oficial:** substitua `static/img/logo.svg` (é um logo provisório) e `static/img/og.png` (imagem de compartilhamento 1200×630). Ícones do app: `icon-192.png` / `icon-512.png`.
- **Cores:** variáveis no topo de `static/css/style.css` (`--roxo`, `--ouro`...).
- **Regiões e fases:** listas `REGIOES` e `FASES` no início de `app.py`.
- Escudos das seleções e logos de patrocinadores: envie pelo painel (PNG/JPG/WEBP/GIF até 6 MB; o servidor confere o conteúdo do arquivo).

## 7. Estrutura
```
app.py              rotas, regras (classificação, artilharia, relógio), tempo real, push, painel
push.py             Web Push (criptografia + VAPID)
seed.py             dados de exemplo / seleções confirmadas / limpar
templates/          páginas do site e do painel (Jinja)
static/css, js      style.css · live.js (tempo real, alertas) · mesa.js (mesa do operador) · sw.js (PWA/push)
tests/              testes automáticos        scripts/  teste de carga
gunicorn.conf.py · Procfile · Dockerfile · docker-compose.yml · deploy-*.conf/service
```

## 8. Observações honestas
- **Os dados de `python seed.py` são fictícios** (jogos, placares, atletas, “estádios de demonstração”). Só os nomes das seleções vêm dos cards do Instagram.
- `seed.py --selecoes`: a região de **Boa Nova** estava cortada no card (assumi Médio Rio de Contas) e a seleção **“Apomirim-BA”** também estava cortada — **não foi cadastrada**; inclua pelo painel. “Olaria” representa Teolândia e foi tratada como Teolândia.
- Fontes (Bebas Neue, Oswald, Inter) carregam do Google Fonts; sem internet o site usa fontes do sistema e continua funcionando.
- SQLite atende bem um campeonato regional em um servidor. Se um dia precisar de vários servidores, migre para PostgreSQL.
