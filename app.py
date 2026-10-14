# -*- coding: utf-8 -*-
"""
Copa Inter Vale Master — plataforma de campeonato em tempo real.

Flask + SQLite. O tempo real usa Server-Sent Events (/stream): todo lance lançado
no painel do operador atualiza as telas abertas sem recarregar a página.
"""
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from functools import wraps
from itertools import groupby

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, stream_with_context,
                   url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import push as webpush

BASE = os.path.abspath(os.path.dirname(__file__))
INSTANCE = os.environ.get("INSTANCE_DIR") or os.path.join(BASE, "instance")
UPLOADS = os.path.join(BASE, "static", "uploads")
os.makedirs(INSTANCE, exist_ok=True)
# UPLOADS_DIR: guarda fotos/escudos enviados no disco persistente (ex.: /dados/uploads) em vez de dentro do código
_ud = os.environ.get("UPLOADS_DIR")
if _ud:
    os.makedirs(_ud, exist_ok=True)
    if not os.path.islink(UPLOADS):
        if os.path.isdir(UPLOADS):
            for _f in os.listdir(UPLOADS):
                _src = os.path.join(UPLOADS, _f)
                if os.path.isfile(_src) and _f != ".gitkeep":
                    os.replace(_src, os.path.join(_ud, _f))
            for _f in os.listdir(UPLOADS):
                os.remove(os.path.join(UPLOADS, _f))
            os.rmdir(UPLOADS)
        try:
            os.symlink(_ud, UPLOADS)
        except FileExistsError:
            pass
os.makedirs(UPLOADS, exist_ok=True)
DB_PATH = os.environ.get("DATABASE", os.path.join(INSTANCE, "copa.db"))


def _secret():
    s = os.environ.get("SECRET_KEY")
    if s:
        return s
    p = os.path.join(INSTANCE, "secret.key")
    if os.path.exists(p):
        with open(p) as fh:
            return fh.read().strip()
    s = secrets.token_hex(32)
    with open(p, "w") as fh:
        fh.write(s)
    return s


app = Flask(__name__)
if os.environ.get("TRUST_PROXY"):  # atrás de nginx/Caddy/Render/Railway: confia no X-Forwarded-*
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=_secret(),
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("PRODUCTION")),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    TEMPLATES_AUTO_RELOAD=not os.environ.get("PRODUCTION"),
)

# ---------------------------------------------------------------- constantes
REGIOES = ["Médio Rio de Contas", "Vale do Jiquiriçá", "Baixo Sul",
           "Recôncavo Baiano", "Sisal", "Chapada Diamantina",
           "Península de Maraú", "Sudoeste da Bahia", "Metropolitana",
           "Costa do Cacau", "Outra"]
FASES = [("grupos", "Fase de grupos"), ("oitavas", "Oitavas de final"),
         ("quartas", "Quartas de final"), ("semi", "Semifinais"),
         ("terceiro", "Disputa do 3º lugar"), ("final", "Final")]
FASE_NOME = dict(FASES)
STATUS = [("agendado", "Agendado"), ("ao_vivo", "Ao vivo"),
          ("encerrado", "Encerrado"), ("adiado", "Adiado")]
STATUS_NOME = dict(STATUS)
POSICOES = ["Goleiro", "Zagueiro", "Lateral", "Volante", "Meia", "Atacante"]
NOTIFICAVEIS = {"gol", "gol_contra", "inicio", "intervalo", "inicio2", "fim", "vermelho"}
EVENTO_ICONE = {"gol": "⚽", "gol_contra": "⚽", "amarelo": "🟨", "vermelho": "🟥",
                "sub": "🔁", "lance": "📣", "inicio": "🟢", "inicio2": "🟢",
                "intervalo": "⏸️", "fim": "🏁"}

CFG_PADRAO = {
    "nome": "Copa Inter Vale Master",
    "edicao": "3ª Edição • 2026",
    "slogan": "A maior competição de veteranos do interior da Bahia!",
    "aviso": "",
    "transmissao_url": "",
    "instagram": "copaintervale",
    "site": "www.copaintervale.com.br",
    "whatsapp": "",
    "email": "",
    "duracao_tempo": "30",
    "regulamento": "Edite o regulamento em Painel › Configurações.",
    "ingressos": "Edite as informações de ingressos em Painel › Configurações.",
    "contato": "Edite os contatos em Painel › Configurações.",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS usuarios(
  id INTEGER PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, senha_hash TEXT NOT NULL,
  perfil TEXT NOT NULL DEFAULT 'operador');
CREATE TABLE IF NOT EXISTS selecoes(
  id INTEGER PRIMARY KEY, nome TEXT NOT NULL, sigla TEXT, cidade TEXT, regiao TEXT,
  grupo TEXT, cor TEXT DEFAULT '#f5b800', escudo TEXT, tecnico TEXT, historico TEXT);
CREATE TABLE IF NOT EXISTS jogadores(
  id INTEGER PRIMARY KEY, selecao_id INTEGER NOT NULL REFERENCES selecoes(id) ON DELETE CASCADE,
  nome TEXT NOT NULL, numero INTEGER, posicao TEXT);
CREATE TABLE IF NOT EXISTS estadios(
  id INTEGER PRIMARY KEY, nome TEXT NOT NULL, cidade TEXT, capacidade INTEGER, endereco TEXT);
CREATE TABLE IF NOT EXISTS arbitros(
  id INTEGER PRIMARY KEY, nome TEXT NOT NULL, federacao TEXT);
CREATE TABLE IF NOT EXISTS jogos(
  id INTEGER PRIMARY KEY, fase TEXT NOT NULL DEFAULT 'grupos', grupo TEXT, rodada INTEGER,
  sel_a INTEGER NOT NULL REFERENCES selecoes(id) ON DELETE CASCADE,
  sel_b INTEGER NOT NULL REFERENCES selecoes(id) ON DELETE CASCADE,
  data_hora TEXT, estadio_id INTEGER REFERENCES estadios(id) ON DELETE SET NULL,
  arbitro_id INTEGER REFERENCES arbitros(id) ON DELETE SET NULL,
  aux1_id INTEGER REFERENCES arbitros(id) ON DELETE SET NULL,
  aux2_id INTEGER REFERENCES arbitros(id) ON DELETE SET NULL,
  transmissao_url TEXT, status TEXT NOT NULL DEFAULT 'agendado',
  periodo TEXT NOT NULL DEFAULT 'pre',
  gols_a INTEGER NOT NULL DEFAULT 0, gols_b INTEGER NOT NULL DEFAULT 0,
  pen_a INTEGER, pen_b INTEGER, rel_base REAL NOT NULL DEFAULT 0, rel_inicio REAL);
CREATE TABLE IF NOT EXISTS eventos(
  id INTEGER PRIMARY KEY, jogo_id INTEGER NOT NULL REFERENCES jogos(id) ON DELETE CASCADE,
  tipo TEXT NOT NULL, minuto TEXT, selecao_id INTEGER, jogador_id INTEGER,
  jogador_nome TEXT, jogador2_nome TEXT, texto TEXT, criado REAL);
CREATE TABLE IF NOT EXISTS noticias(
  id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, resumo TEXT, texto TEXT, imagem TEXT, criado TEXT);
CREATE TABLE IF NOT EXISTS patrocinadores(
  id INTEGER PRIMARY KEY, nome TEXT NOT NULL, nivel TEXT DEFAULT 'Apoio', logo TEXT,
  url TEXT, ordem INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS midia(
  id INTEGER PRIMARY KEY, tipo TEXT NOT NULL DEFAULT 'foto', legenda TEXT, arquivo TEXT,
  url TEXT, criado TEXT);
CREATE TABLE IF NOT EXISTS push_subs(
  endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
  todos INTEGER NOT NULL DEFAULT 0, selecoes TEXT NOT NULL DEFAULT '[]', criado REAL);
CREATE INDEX IF NOT EXISTS ix_jogos_status ON jogos(status);
CREATE INDEX IF NOT EXISTS ix_eventos_jogo ON eventos(jogo_id);
"""

JOGO_SQL = """
SELECT j.*,
  a.nome AS nome_a, a.sigla AS sigla_a, a.cor AS cor_a, a.escudo AS escudo_a, a.regiao AS regiao_a,
  b.nome AS nome_b, b.sigla AS sigla_b, b.cor AS cor_b, b.escudo AS escudo_b, b.regiao AS regiao_b,
  e.nome AS estadio_nome, e.cidade AS estadio_cidade,
  ar.nome AS arbitro_nome, x1.nome AS aux1_nome, x2.nome AS aux2_nome
FROM jogos j
JOIN selecoes a ON a.id = j.sel_a
JOIN selecoes b ON b.id = j.sel_b
LEFT JOIN estadios e ON e.id = j.estadio_id
LEFT JOIN arbitros ar ON ar.id = j.arbitro_id
LEFT JOIN arbitros x1 ON x1.id = j.aux1_id
LEFT JOIN arbitros x2 ON x2.id = j.aux2_id
"""
ORDEM_DATA = "ORDER BY CASE WHEN j.data_hora IS NULL OR j.data_hora='' THEN 1 ELSE 0 END, j.data_hora, j.id"


# ---------------------------------------------------------------- banco
def conectar():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def get_db():
    if "db" not in g:
        g.db = conectar()
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = conectar()
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.execute("INSERT OR IGNORE INTO meta(chave, valor) VALUES('versao', '1')")
    novo_admin = None
    if not db.execute("SELECT 1 FROM usuarios").fetchone():
        usuario = os.environ.get("ADMIN_USER", "admin")
        senha = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        try:
            db.execute("INSERT INTO usuarios(usuario, senha_hash, perfil) VALUES(?,?,'admin')",
                       (usuario, generate_password_hash(senha)))
            novo_admin = (usuario, senha)
        except sqlite3.IntegrityError:  # outro worker do gunicorn criou ao mesmo tempo
            pass
    db.commit()
    db.close()
    if novo_admin:
        print("\n" + "=" * 60)
        print(" ADMINISTRADOR CRIADO — guarde estes dados:")
        print(f"   usuário: {novo_admin[0]}")
        print(f"   senha:   {novo_admin[1]}")
        print(" (troque a senha em Painel › Usuários)")
        print("=" * 60 + "\n")


def bump(db):
    """Avisa todos os clientes conectados que algo mudou."""
    db.execute("UPDATE meta SET valor = CAST(valor AS INTEGER) + 1 WHERE chave='versao'")
    db.commit()


def get_cfg():
    if "cfg" not in g:
        c = dict(CFG_PADRAO)
        c.update({r["chave"]: r["valor"] for r in get_db().execute("SELECT * FROM config")})
        g.cfg = c
    return g.cfg


def duracao():
    try:
        return max(1, int(get_cfg().get("duracao_tempo") or 30))
    except ValueError:
        return 30


# ---------------------------------------------------------------- relógio de jogo
def total_min(j, now=None):
    now = now or time.time()
    t = j["rel_base"] or 0
    if j["rel_inicio"]:
        t += (now - j["rel_inicio"]) / 60.0
    return t


def fmt_min(j, dur, now=None):
    if j["status"] != "ao_vivo":
        return ""
    p = j["periodo"]
    if p == "intervalo":
        return "INT"
    if p == "penaltis":
        return "PÊN"
    if p not in ("1T", "2T"):
        return ""
    m = int(total_min(j, now)) + 1
    lim = dur if p == "1T" else 2 * dur
    return f"{lim}+{m - lim}'" if m > lim else f"{m}'"


# ---------------------------------------------------------------- consultas
def listar_jogos(db, where="", params=(), order=ORDEM_DATA, limit=None):
    sql = JOGO_SQL + (" WHERE " + where if where else "") + " " + order
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db.execute(sql, params)]


def um_jogo(db, jid):
    r = db.execute(JOGO_SQL + " WHERE j.id=?", (jid,)).fetchone()
    return dict(r) if r else None


def vencedor(j):
    if j["status"] != "encerrado":
        return None
    if j["gols_a"] > j["gols_b"]:
        return j["sel_a"]
    if j["gols_b"] > j["gols_a"]:
        return j["sel_b"]
    if j["pen_a"] is not None and j["pen_b"] is not None and j["pen_a"] != j["pen_b"]:
        return j["sel_a"] if j["pen_a"] > j["pen_b"] else j["sel_b"]
    return None


def perdedor(j):
    w = vencedor(j)
    if w is None:
        return None
    return j["sel_b"] if w == j["sel_a"] else j["sel_a"]


def calc_classificacao(db):
    sels = {}
    for r in db.execute("SELECT * FROM selecoes"):
        d = dict(r)
        d.update(j=0, v=0, e=0, d=0, gp=0, gc=0, pts=0, ca=0, cv=0)
        sels[d["id"]] = d
    for j in db.execute("SELECT * FROM jogos WHERE fase='grupos' AND status IN ('ao_vivo','encerrado')"):
        a, b = sels.get(j["sel_a"]), sels.get(j["sel_b"])
        if not a or not b:
            continue
        ga, gb = j["gols_a"], j["gols_b"]
        a["j"] += 1
        b["j"] += 1
        a["gp"] += ga
        a["gc"] += gb
        b["gp"] += gb
        b["gc"] += ga
        if ga > gb:
            a["v"] += 1
            b["d"] += 1
            a["pts"] += 3
        elif gb > ga:
            b["v"] += 1
            a["d"] += 1
            b["pts"] += 3
        else:
            a["e"] += 1
            b["e"] += 1
            a["pts"] += 1
            b["pts"] += 1
    q = """SELECT e.tipo, e.selecao_id, COUNT(*) n FROM eventos e JOIN jogos j ON j.id=e.jogo_id
           WHERE j.fase='grupos' AND e.tipo IN ('amarelo','vermelho') GROUP BY e.tipo, e.selecao_id"""
    for r in db.execute(q):
        s = sels.get(r["selecao_id"])
        if s:
            s["ca" if r["tipo"] == "amarelo" else "cv"] += r["n"]
    for s in sels.values():
        s["sg"] = s["gp"] - s["gc"]
        s["cart"] = s["ca"] + 3 * s["cv"]
    ordenado = sorted(sels.values(), key=lambda s: (
        s["grupo"] or "~", -s["pts"], -s["v"], -s["sg"], -s["gp"], s["cart"], s["nome"]))
    grupos = {}
    for s in ordenado:
        grupos.setdefault(s["grupo"] or "—", []).append(s)
    for lst in grupos.values():
        for i, s in enumerate(lst, 1):
            s["pos"] = i
    return grupos


def calc_artilharia(db, limite=30):
    q = """SELECT e.jogador_nome AS nome, e.selecao_id, s.nome AS selecao, s.sigla, s.cor, s.escudo,
                  COUNT(*) AS gols
           FROM eventos e JOIN selecoes s ON s.id = e.selecao_id
           WHERE e.tipo='gol' AND e.jogador_nome IS NOT NULL AND e.jogador_nome <> ''
           GROUP BY e.jogador_nome, e.selecao_id ORDER BY gols DESC, nome LIMIT ?"""
    return [dict(r) for r in db.execute(q, (limite,))]


def calc_disciplina(db, limite=10):
    q = """SELECT e.jogador_nome AS nome, s.nome AS selecao, s.sigla, s.cor, s.escudo,
                  SUM(e.tipo='amarelo') AS amarelos, SUM(e.tipo='vermelho') AS vermelhos
           FROM eventos e JOIN selecoes s ON s.id = e.selecao_id
           WHERE e.tipo IN ('amarelo','vermelho') AND e.jogador_nome IS NOT NULL AND e.jogador_nome <> ''
           GROUP BY e.jogador_nome, e.selecao_id
           ORDER BY vermelhos DESC, amarelos DESC, nome LIMIT ?"""
    return [dict(r) for r in db.execute(q, (limite,))]


def calc_campeao(db):
    f = db.execute(JOGO_SQL + " WHERE j.fase='final' AND j.status='encerrado' ORDER BY j.id DESC LIMIT 1").fetchone()
    if not f:
        return None
    f = dict(f)
    w = vencedor(f)
    if w is None:
        return None
    s = db.execute("SELECT * FROM selecoes WHERE id=?", (w,)).fetchone()
    return dict(s) if s else None


def recalc_placar(db, jid):
    j = db.execute("SELECT sel_a, sel_b FROM jogos WHERE id=?", (jid,)).fetchone()
    r = db.execute("""SELECT
      COALESCE(SUM((tipo='gol' AND selecao_id=:a) OR (tipo='gol_contra' AND selecao_id=:b)),0),
      COALESCE(SUM((tipo='gol' AND selecao_id=:b) OR (tipo='gol_contra' AND selecao_id=:a)),0)
      FROM eventos WHERE jogo_id=:id""", {"a": j["sel_a"], "b": j["sel_b"], "id": jid}).fetchone()
    db.execute("UPDATE jogos SET gols_a=?, gols_b=? WHERE id=?", (r[0], r[1], jid))


# ---------------------------------------------------------------- filtros / contexto
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


def _parse(v):
    try:
        return datetime.strptime((v or "")[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


@app.template_filter("dt")
def f_dt(v):
    d = _parse(v)
    if not d:
        return "Data a definir"
    return f"{DIAS[d.weekday()][:3]}, {d:%d/%m} • {d:%H:%M}"


@app.template_filter("hora")
def f_hora(v):
    d = _parse(v)
    return f"{d:%H:%M}" if d else "--:--"


@app.template_filter("dia")
def f_dia(v):
    d = _parse(v)
    if not d:
        return "Data a definir"
    return f"{DIAS[d.weekday()].capitalize()}, {d.day} de {MESES[d.month - 1]}"


@app.template_filter("datainput")
def f_datainput(v):
    d = _parse(v)
    return d.strftime("%Y-%m-%dT%H:%M") if d else ""


@app.template_filter("datacurta")
def f_datacurta(v):
    try:
        d = datetime.strptime((v or "")[:10], "%Y-%m-%d")
        return f"{d.day} de {MESES[d.month - 1]} de {d.year}"
    except ValueError:
        return ""


_YT = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|live/|shorts/))([\w-]{11})")


@app.template_filter("youtube")
def f_youtube(url):
    m = _YT.search(url or "")
    return m.group(1) if m else None


@app.context_processor
def contexto():
    db = get_db()
    live = db.execute("SELECT COUNT(*) FROM jogos WHERE status='ao_vivo'").fetchone()[0]
    spons = [dict(r) for r in db.execute(
        "SELECT * FROM patrocinadores ORDER BY ordem, nome")]
    return dict(cfg=get_cfg(), live_count=live, patrocinadores_all=spons, FASE_NOME=FASE_NOME,
                STATUS_NOME=STATUS_NOME, EVENTO_ICONE=EVENTO_ICONE, now_ts=time.time(),
                dur=duracao(), csrf_token=csrf_token, usuario_atual=session.get("usuario"),
                perfil_atual=session.get("perfil"), REGIOES=REGIOES, entidades=ENT)


@app.after_request
def cabecalhos(resp):
    resp.headers["X-Server-Time"] = str(time.time())
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------- segurança
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def csrf_check():
    tok = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not tok or not secrets.compare_digest(tok, session.get("csrf", "")):
        abort(400, "Sessão expirada. Recarregue a página e tente de novo.")


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w


def role_required(*perfis):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if not session.get("uid"):
                if request.method == "POST":
                    abort(401)
                return redirect(url_for("login", next=request.path))
            if session.get("perfil") != "admin" and session.get("perfil") not in perfis:
                abort(403)
            return f(*a, **k)
        return w
    return deco


_tentativas = {}


def _bloqueado(ip):
    agora = time.time()
    lst = [t for t in _tentativas.get(ip, []) if agora - t < 300]
    _tentativas[ip] = lst
    return len(lst) >= 6


# ================================================================ PÁGINAS PÚBLICAS
@app.route("/")
def index():
    db = get_db()
    ao_vivo = listar_jogos(db, "j.status='ao_vivo'")
    for j in ao_vivo:
        u = db.execute("SELECT texto FROM eventos WHERE jogo_id=? ORDER BY id DESC LIMIT 1",
                       (j["id"],)).fetchone()
        j["ultimo"] = u["texto"] if u else None
    proximos = listar_jogos(db, "j.status='agendado' AND j.data_hora<>''",
                            order="ORDER BY j.data_hora, j.id", limit=6)
    resultados = listar_jogos(db, "j.status='encerrado'",
                              order="ORDER BY j.data_hora DESC, j.id DESC", limit=6)
    feed = [dict(r) for r in db.execute("""
        SELECT e.*, a.nome AS nome_a, b.nome AS nome_b FROM eventos e
        JOIN jogos j ON j.id=e.jogo_id JOIN selecoes a ON a.id=j.sel_a JOIN selecoes b ON b.id=j.sel_b
        WHERE e.tipo IN ('gol','gol_contra','vermelho','inicio','intervalo','inicio2','fim','lance')
        ORDER BY e.id DESC LIMIT 8""")]
    noticias = [dict(r) for r in db.execute("SELECT * FROM noticias ORDER BY id DESC LIMIT 3")]
    top = calc_artilharia(db, 3)
    return render_template("index.html", ao_vivo=ao_vivo, proximos=proximos, resultados=resultados,
                           feed=feed, noticias=noticias, top=top, campeao=calc_campeao(db))


@app.route("/jogos")
def jogos():
    db = get_db()
    f = {k: request.args.get(k, "") for k in ("fase", "selecao", "regiao", "dia", "status")}
    where, params = ["1=1"], []
    if f["fase"]:
        where.append("j.fase=?")
        params.append(f["fase"])
    if f["selecao"].isdigit():
        where.append("(j.sel_a=? OR j.sel_b=?)")
        params += [int(f["selecao"])] * 2
    if f["regiao"]:
        where.append("(a.regiao=? OR b.regiao=?)")
        params += [f["regiao"]] * 2
    if f["dia"]:
        where.append("substr(j.data_hora,1,10)=?")
        params.append(f["dia"])
    if f["status"]:
        where.append("j.status=?")
        params.append(f["status"])
    lista = listar_jogos(db, " AND ".join(where), params)
    dias = [r[0] for r in db.execute(
        "SELECT DISTINCT substr(data_hora,1,10) FROM jogos WHERE data_hora<>'' ORDER BY 1")]
    grupos = [(k, list(v)) for k, v in groupby(lista, key=lambda j: (j["data_hora"] or "")[:10])]
    return render_template("jogos.html", grupos=grupos, f=f, dias=dias,
                           selecoes=db.execute("SELECT id, nome FROM selecoes ORDER BY nome").fetchall(),
                           fases=FASES, status_lista=STATUS, total=len(lista))


@app.route("/jogo/<int:jid>")
def jogo(jid):
    db = get_db()
    j = um_jogo(db, jid)
    if not j:
        abort(404)
    eventos = [dict(r) for r in db.execute(
        "SELECT * FROM eventos WHERE jogo_id=? ORDER BY id DESC", (jid,))]
    return render_template("jogo.html", j=j, eventos=eventos,
                           video=f_youtube(j["transmissao_url"] or get_cfg()["transmissao_url"]),
                           link_tv=j["transmissao_url"] or get_cfg()["transmissao_url"])


@app.route("/classificacao")
def classificacao():
    db = get_db()
    fases = {}
    for j in listar_jogos(db, "j.fase<>'grupos'", order="ORDER BY j.id"):
        fases.setdefault(j["fase"], []).append(j)
    ordem = [f for f, _ in FASES if f in fases]
    provisorio = bool(db.execute(
        "SELECT 1 FROM jogos WHERE fase='grupos' AND status='ao_vivo'").fetchone())
    return render_template("classificacao.html", grupos=calc_classificacao(db), fases=fases,
                           ordem=ordem, provisorio=provisorio)


@app.route("/selecoes")
def selecoes():
    db = get_db()
    reg = request.args.get("regiao", "")
    sql = "SELECT * FROM selecoes" + (" WHERE regiao=?" if reg else "") + " ORDER BY nome"
    lista = [dict(r) for r in db.execute(sql, (reg,) if reg else ())]
    regioes = [r[0] for r in db.execute(
        "SELECT DISTINCT regiao FROM selecoes WHERE regiao<>'' ORDER BY 1")]
    return render_template("selecoes.html", lista=lista, regioes=regioes, reg=reg)


@app.route("/selecao/<int:sid>")
def selecao(sid):
    db = get_db()
    s = db.execute("SELECT * FROM selecoes WHERE id=?", (sid,)).fetchone()
    if not s:
        abort(404)
    s = dict(s)
    elenco = [dict(r) for r in db.execute(
        "SELECT * FROM jogadores WHERE selecao_id=? ORDER BY COALESCE(numero, 999), nome", (sid,))]
    jg = listar_jogos(db, "j.sel_a=? OR j.sel_b=?", (sid, sid))
    grupos = calc_classificacao(db)
    linha = next((x for lst in grupos.values() for x in lst if x["id"] == sid), None)
    arts = [a for a in calc_artilharia(db, 100) if a["selecao_id"] == sid][:5]
    return render_template("selecao.html", s=s, elenco=elenco, jogos=jg, linha=linha, arts=arts)


@app.route("/artilharia")
def artilharia():
    db = get_db()
    grupos = calc_classificacao(db)
    todas = [x for lst in grupos.values() for x in lst if x["j"] > 0]
    ataque = sorted(todas, key=lambda s: (-s["gp"], s["nome"]))[:5]
    defesa = sorted(todas, key=lambda s: (s["gc"], s["nome"]))[:5]
    cartoes = sorted([x for lst in grupos.values() for x in lst if x["cart"] > 0],
                     key=lambda s: (-s["cart"], s["nome"]))[:5]
    total_gols = db.execute("SELECT COALESCE(SUM(gols_a+gols_b),0) FROM jogos").fetchone()[0]
    total_jogos = db.execute("SELECT COUNT(*) FROM jogos WHERE status IN ('ao_vivo','encerrado')").fetchone()[0]
    return render_template("artilharia.html", artilheiros=calc_artilharia(db, 20), ataque=ataque,
                           defesa=defesa, cartoes=cartoes, disciplina=calc_disciplina(db),
                           total_gols=total_gols, total_jogos=total_jogos)


@app.route("/noticias")
def noticias():
    db = get_db()
    return render_template("noticias.html",
                           lista=[dict(r) for r in db.execute("SELECT * FROM noticias ORDER BY id DESC")],
                           midia=[dict(r) for r in db.execute("SELECT * FROM midia ORDER BY id DESC")])


@app.route("/noticia/<int:nid>")
def noticia(nid):
    n = get_db().execute("SELECT * FROM noticias WHERE id=?", (nid,)).fetchone()
    if not n:
        abort(404)
    return render_template("noticia.html", n=dict(n))


@app.route("/patrocinadores")
def patrocinadores():
    return render_template("patrocinadores.html")


@app.route("/info")
def info():
    est = [dict(r) for r in get_db().execute("SELECT * FROM estadios ORDER BY nome")]
    return render_template("info.html", estadios=est)


# ---------------------------------------------------------------- tempo real
class Hub:
    """Um único "vigia" por processo lê o banco ~1x/s e entrega as novidades a TODAS as conexões
    abertas. Assim 2.000 torcedores conectados custam 1 consulta por segundo (não 2.000)."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.msgs = []  # (seq, evento, dados)
        self._lock = threading.Lock()
        self._thread = None

    def iniciar(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._vigiar, name="hub-tempo-real", daemon=True)
                self._thread.start()

    def _publicar(self, lista):
        with self.cond:
            for ev, dados in lista:
                self.seq += 1
                self.msgs.append((self.seq, ev, dados))
            self.msgs = self.msgs[-100:]
            self.cond.notify_all()

    def _vigiar(self):
        c = None
        ver = ult = None
        while True:
            try:
                if c is None:
                    c = conectar()
                v = int(c.execute("SELECT valor FROM meta WHERE chave='versao'").fetchone()[0])
                if ver is None:
                    ver = v
                    ult = c.execute("SELECT COALESCE(MAX(id),0) FROM eventos").fetchone()[0]
                elif v != ver:
                    ver = v
                    saida = []
                    mx = c.execute("SELECT COALESCE(MAX(id),0) FROM eventos").fetchone()[0]
                    if mx < ult:  # lançamento excluído
                        ult = mx
                    for e in c.execute("""SELECT e.id, e.jogo_id, e.tipo, e.texto, e.minuto, j.sel_a, j.sel_b,
                                           j.gols_a, j.gols_b FROM eventos e JOIN jogos j ON j.id=e.jogo_id
                                           WHERE e.id>? ORDER BY e.id""", (ult,)).fetchall():
                        ult = e["id"]
                        if e["tipo"] in NOTIFICAVEIS:
                            saida.append(("lance", dict(e)))
                    saida.append(("update", {"versao": v}))
                    self._publicar(saida)
            except Exception:  # banco ocupado/fechado: tenta de novo no próximo ciclo
                try:
                    c and c.close()
                except Exception:
                    pass
                c = None
            time.sleep(0.7)

    def ouvir(self):
        """Gerador SSE de uma conexão."""
        self.iniciar()
        with self.cond:
            ultimo = self.seq
            ver = None
        try:
            c = conectar()
            ver = int(c.execute("SELECT valor FROM meta WHERE chave='versao'").fetchone()[0])
            c.close()
        except Exception:
            pass
        yield "retry: 3000\n\n"
        yield f"event: hello\ndata: {json.dumps({'versao': ver})}\n\n"
        while True:
            with self.cond:
                if not self.cond.wait_for(lambda: self.seq > ultimo, timeout=15):
                    novas = None
                else:
                    novas = [m for m in self.msgs if m[0] > ultimo]
                    ultimo = self.seq
            if novas is None:
                yield ": ping\n\n"
                continue
            for _, ev, dados in novas:
                yield f"event: {ev}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"


HUB = Hub()


@app.route("/stream")
def stream():
    return Response(stream_with_context(HUB.ouvir()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- notificações push (site fechado)
PUSH_PRIV, PUSH_PUB = webpush.carregar_chave(os.path.join(INSTANCE, "vapid_private.pem"))
PUSH_TITULO = {"gol": "⚽ GOL!", "gol_contra": "⚽ Gol contra", "inicio": "🟢 Começou!", "inicio2": "🟢 2º tempo",
               "intervalo": "⏸️ Intervalo", "fim": "🏁 Fim de jogo", "vermelho": "🟥 Expulsão"}


def _push_assunto():
    site = (os.environ.get("SITE_URL") or "https://" + CFG_PADRAO["site"]).strip()
    return site if site.startswith("http") else "https://" + site


def push_eventos(eventos):
    """Roda em segundo plano depois de cada lançamento da mesa."""
    try:
        c = conectar()
        subs = [dict(r) for r in c.execute("SELECT * FROM push_subs")]
        c.close()
        for ev in eventos:
            alvo = [s for s in subs if s["todos"] or {ev["sel_a"], ev["sel_b"]} & set(json.loads(s["selecoes"]))]
            if not alvo:
                continue
            carga = {"title": PUSH_TITULO.get(ev["tipo"], "Copa Inter Vale"), "body": ev["texto"],
                     "url": f"/jogo/{ev['jogo_id']}", "tag": f"jogo-{ev['jogo_id']}"}

            def limpar(mortas):
                cc = conectar()
                cc.executemany("DELETE FROM push_subs WHERE endpoint=?", [(m,) for m in mortas])
                cc.commit()
                cc.close()
            webpush.enviar_para_todos(PUSH_PRIV, PUSH_PUB, _push_assunto(), alvo, carga, limpar)
    except Exception as ex:  # push nunca pode derrubar o placar
        app.logger.warning("push falhou: %s", ex)


@app.get("/api/push/chave")
def push_chave():
    return jsonify(chave=PUSH_PUB)


@app.post("/api/push/inscrever")
def push_inscrever():
    d = request.get_json(silent=True) or {}
    s = d.get("subscription") or {}
    k = s.get("keys") or {}
    ep = s.get("endpoint", "")
    if not (isinstance(ep, str) and ep.startswith("https://") and len(ep) < 600
            and isinstance(k.get("p256dh"), str) and isinstance(k.get("auth"), str)):
        return jsonify(ok=False, erro="Inscrição inválida."), 400
    try:
        if len(webpush.b64u_dec(k["p256dh"])) != 65 or len(webpush.b64u_dec(k["auth"])) != 16:
            raise ValueError
    except Exception:
        return jsonify(ok=False, erro="Chaves inválidas."), 400
    sels = sorted({int(x) for x in (d.get("selecoes") or []) if str(x).isdigit()})[:64]
    db = get_db()
    db.execute("INSERT INTO push_subs(endpoint, p256dh, auth, todos, selecoes, criado) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, "
               "todos=excluded.todos, selecoes=excluded.selecoes",
               (ep, k["p256dh"], k["auth"], 1 if d.get("todos") else 0, json.dumps(sels), time.time()))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/push/cancelar")
def push_cancelar():
    ep = (request.get_json(silent=True) or {}).get("endpoint", "")
    db = get_db()
    db.execute("DELETE FROM push_subs WHERE endpoint=?", (ep,))
    db.commit()
    return jsonify(ok=True)


@app.route("/api/versao")
def api_versao():
    v = get_db().execute("SELECT valor FROM meta WHERE chave='versao'").fetchone()[0]
    return jsonify(versao=int(v))


@app.route("/api/estado")
def api_estado():
    """JSON público com os jogos ao vivo — útil para telões e widgets externos."""
    db = get_db()
    d = duracao()
    out = []
    for j in listar_jogos(db, "j.status='ao_vivo'"):
        out.append(dict(id=j["id"], fase=j["fase"], a=j["nome_a"], b=j["nome_b"],
                        gols_a=j["gols_a"], gols_b=j["gols_b"], minuto=fmt_min(j, d),
                        periodo=j["periodo"], estadio=j["estadio_nome"]))
    return jsonify(ao_vivo=out, servidor=time.time())


@app.route("/sw.js")
def service_worker():
    resp = app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/manifest.webmanifest")
def manifest():
    c = get_cfg()
    m = {"name": c["nome"], "short_name": "Inter Vale", "description": c["slogan"],
         "start_url": "/", "scope": "/", "display": "standalone", "orientation": "portrait",
         "background_color": "#120628", "theme_color": "#1a0b3b", "lang": "pt-BR",
         "icons": [{"src": url_for("static", filename="img/icon-192.png"), "sizes": "192x192",
                    "type": "image/png", "purpose": "any maskable"},
                   {"src": url_for("static", filename="img/icon-512.png"), "sizes": "512x512",
                    "type": "image/png", "purpose": "any maskable"}]}
    return Response(json.dumps(m), mimetype="application/manifest+json")


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /admin\nDisallow: /stream\n"
                    f"Sitemap: {url_for('sitemap', _external=True)}\n", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    paginas = ["index", "jogos", "classificacao", "selecoes", "artilharia", "noticias",
               "patrocinadores", "info"]
    urls = [url_for(p, _external=True) for p in paginas]
    db = get_db()
    urls += [url_for("selecao", sid=r[0], _external=True) for r in db.execute("SELECT id FROM selecoes")]
    urls += [url_for("jogo", jid=r[0], _external=True) for r in db.execute("SELECT id FROM jogos")]
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    xml += "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
    return Response(xml, mimetype="application/xml")


@app.errorhandler(404)
def e404(_):
    return render_template("erro.html", codigo=404, msg="Essa página saiu de campo."), 404


@app.errorhandler(403)
def e403(_):
    return render_template("erro.html", codigo=403, msg="Seu perfil não tem acesso a esta área."), 403


@app.errorhandler(400)
def e400(e):
    return render_template("erro.html", codigo=400, msg=getattr(e, "description", "Requisição inválida.")), 400


@app.errorhandler(413)
def e413(_):
    return render_template("erro.html", codigo=413, msg="Arquivo muito grande (máximo 6 MB)."), 413


# ================================================================ ADMIN
@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        csrf_check()
        ip = request.remote_addr or "?"  # com TRUST_PROXY o ProxyFix já devolve o IP real do visitante
        if _bloqueado(ip):
            flash("Muitas tentativas. Aguarde alguns minutos.", "erro")
            return render_template("admin/login.html"), 429
        u = get_db().execute("SELECT * FROM usuarios WHERE usuario=?",
                             (request.form.get("usuario", "").strip(),)).fetchone()
        if u and check_password_hash(u["senha_hash"], request.form.get("senha", "")):
            session.clear()
            session.update(uid=u["id"], usuario=u["usuario"], perfil=u["perfil"])
            session.permanent = True
            prox = request.args.get("next", "")
            return redirect(prox if prox.startswith("/admin") else url_for("admin_index"))
        _tentativas.setdefault(ip, []).append(time.time())
        flash("Usuário ou senha incorretos.", "erro")
    return render_template("admin/login.html")


@app.route("/admin/logout", methods=["POST"])
def logout():
    csrf_check()
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin/")
@login_required
def admin_index():
    db = get_db()
    cont = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("selecoes", "jogadores", "jogos", "noticias", "patrocinadores")}
    return render_template("admin/index.html", cont=cont,
                           ao_vivo=listar_jogos(db, "j.status='ao_vivo'"),
                           proximos=listar_jogos(db, "j.status='agendado'", limit=5),
                           entidades=ENT)


@app.route("/admin/config", methods=["GET", "POST"])
@role_required("admin")
def admin_config():
    db = get_db()
    if request.method == "POST":
        csrf_check()
        for k in CFG_PADRAO:
            if k in request.form:
                db.execute("INSERT INTO config(chave, valor) VALUES(?,?) "
                           "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                           (k, request.form[k].strip()))
        bump(db)
        flash("Configurações salvas.", "ok")
        return redirect(url_for("admin_config"))
    return render_template("admin/config.html")


# ---------------------------------------------------------------- upload seguro
EXT_OK = {"png": [b"\x89PNG"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
          "gif": [b"GIF8"], "webp": [b"RIFF"]}


def salvar_upload(f):
    if not f or not f.filename:
        return None
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in EXT_OK:
        raise ValueError("Formato de imagem não permitido (use PNG, JPG, WEBP ou GIF).")
    cab = f.stream.read(12)
    f.stream.seek(0)
    if not any(cab.startswith(m) for m in EXT_OK[ext]):
        raise ValueError("O arquivo enviado não parece ser uma imagem válida.")
    nome = f"{secrets.token_hex(8)}.{ext}"
    f.save(os.path.join(UPLOADS, nome))
    return "uploads/" + nome


# ---------------------------------------------------------------- CRUD genérico
def opts(kind):
    q = {"selecoes": "SELECT id, nome FROM selecoes ORDER BY nome",
         "estadios": "SELECT id, nome || COALESCE(' – ' || cidade, '') FROM estadios ORDER BY nome",
         "arbitros": "SELECT id, nome FROM arbitros ORDER BY nome"}[kind]
    return [(r[0], r[1]) for r in get_db().execute(q)]


def C(nome, label, tipo="text", **kw):
    return dict(nome=nome, label=label, tipo=tipo, **kw)


ENT = {
    "selecoes": dict(
        titulo="Seleções", singular="seleção", perfis=["admin"], icone="🛡️",
        campos=[C("nome", "Nome da seleção", req=True), C("sigla", "Sigla (3 letras)", maxlength=4),
                C("cidade", "Cidade"), C("regiao", "Região", "select", opcoes=REGIOES),
                C("grupo", "Grupo", "select", opcoes=["A", "B", "C", "D", "E", "F", "G", "H"]),
                C("cor", "Cor do escudo", "color"), C("escudo", "Escudo (imagem)", "file"),
                C("tecnico", "Técnico / comissão técnica"),
                C("historico", "Histórico", "textarea")],
        sql="SELECT * FROM selecoes ORDER BY nome",
        colunas=[("nome", "Seleção"), ("cidade", "Cidade"), ("regiao", "Região"), ("grupo", "Grupo")]),
    "jogadores": dict(
        titulo="Jogadores", singular="jogador", perfis=["admin"], icone="👟",
        campos=[C("selecao_id", "Seleção", "select", opcoes="selecoes", fk=True, req=True),
                C("nome", "Nome", req=True), C("numero", "Número da camisa", "number"),
                C("posicao", "Posição", "select", opcoes=POSICOES)],
        sql="SELECT j.*, s.nome AS selecao FROM jogadores j JOIN selecoes s ON s.id=j.selecao_id "
            "{filtro} ORDER BY s.nome, COALESCE(j.numero, 999), j.nome",
        filtro=("selecao", "j.selecao_id"),
        colunas=[("selecao", "Seleção"), ("numero", "Nº"), ("nome", "Nome"), ("posicao", "Posição")]),
    "jogos": dict(
        titulo="Jogos", singular="jogo", perfis=["admin"], icone="📅",
        campos=[C("fase", "Fase", "select", opcoes=FASES, req=True),
                C("grupo", "Grupo (fase de grupos)"), C("rodada", "Rodada", "number"),
                C("sel_a", "Seleção A (mandante)", "select", opcoes="selecoes", fk=True, req=True),
                C("sel_b", "Seleção B (visitante)", "select", opcoes="selecoes", fk=True, req=True),
                C("data_hora", "Data e hora", "datetime"),
                C("estadio_id", "Estádio", "select", opcoes="estadios", fk=True),
                C("arbitro_id", "Árbitro central", "select", opcoes="arbitros", fk=True),
                C("aux1_id", "Auxiliar 1", "select", opcoes="arbitros", fk=True),
                C("aux2_id", "Auxiliar 2", "select", opcoes="arbitros", fk=True),
                C("transmissao_url", "Link da transmissão (YouTube/TV) — deste jogo", "url"),
                C("status", "Situação", "select", opcoes=STATUS,
                  ajuda="Para iniciar/encerrar use a Mesa do operador.")],
        sql="SELECT j.id, j.fase, j.grupo, j.data_hora, j.status, a.nome || ' x ' || b.nome AS confronto "
            "FROM jogos j JOIN selecoes a ON a.id=j.sel_a JOIN selecoes b ON b.id=j.sel_b "
            "ORDER BY CASE WHEN j.data_hora IS NULL OR j.data_hora='' THEN 1 ELSE 0 END, j.data_hora, j.id",
        colunas=[("data_hora", "Data"), ("confronto", "Jogo"), ("fase", "Fase"), ("status", "Situação")],
        extra_acao="mesa"),
    "estadios": dict(
        titulo="Estádios", singular="estádio", perfis=["admin"], icone="🏟️",
        campos=[C("nome", "Nome", req=True), C("cidade", "Cidade"),
                C("capacidade", "Capacidade", "number"), C("endereco", "Endereço")],
        sql="SELECT * FROM estadios ORDER BY nome",
        colunas=[("nome", "Estádio"), ("cidade", "Cidade"), ("capacidade", "Capacidade")]),
    "arbitros": dict(
        titulo="Árbitros", singular="árbitro", perfis=["admin"], icone="🟨",
        campos=[C("nome", "Nome", req=True), C("federacao", "Federação / Quadro")],
        sql="SELECT * FROM arbitros ORDER BY nome",
        colunas=[("nome", "Nome"), ("federacao", "Federação")]),
    "noticias": dict(
        titulo="Notícias", singular="notícia", perfis=["editor"], icone="📰",
        campos=[C("titulo", "Título", req=True), C("resumo", "Resumo (aparece nos cards)"),
                C("texto", "Texto", "textarea", linhas=10), C("imagem", "Imagem de capa", "file")],
        sql="SELECT * FROM noticias ORDER BY id DESC",
        colunas=[("criado", "Data"), ("titulo", "Título")], ao_criar="criado_agora"),
    "midia": dict(
        titulo="Fotos e vídeos", singular="mídia", perfis=["editor"], icone="🖼️",
        campos=[C("tipo", "Tipo", "select", opcoes=[("foto", "Foto"), ("video", "Vídeo do YouTube"),
                                                    ("instagram", "Post do Instagram")], req=True),
                C("legenda", "Legenda"), C("arquivo", "Foto (se tipo = foto)", "file"),
                C("url", "Link (vídeo do YouTube ou post do Instagram)", "url")],
        sql="SELECT * FROM midia ORDER BY id DESC",
        colunas=[("tipo", "Tipo"), ("legenda", "Legenda")], ao_criar="criado_agora"),
    "patrocinadores": dict(
        titulo="Patrocinadores", singular="patrocinador", perfis=["editor"], icone="🤝",
        campos=[C("nome", "Nome", req=True),
                C("nivel", "Nível", "select", opcoes=["Master", "Ouro", "Prata", "Apoio"]),
                C("logo", "Logomarca", "file"), C("url", "Site / Instagram", "url"),
                C("ordem", "Ordem de exibição", "number")],
        sql="SELECT * FROM patrocinadores ORDER BY ordem, nome",
        colunas=[("nivel", "Nível"), ("nome", "Nome")]),
    "usuarios": dict(
        titulo="Usuários", singular="usuário", perfis=["admin"], icone="🔐",
        campos=[C("usuario", "Usuário (login)", req=True),
                C("perfil", "Perfil", "select", opcoes=[
                    ("admin", "Administrador"), ("operador", "Operador de jogo"),
                    ("editor", "Editor de conteúdo")], req=True),
                C("senha", "Senha", "password", ajuda="Na edição, deixe em branco para manter a atual.")],
        sql="SELECT * FROM usuarios ORDER BY usuario",
        colunas=[("usuario", "Usuário"), ("perfil", "Perfil")]),
}


def _ent(nome):
    e = ENT.get(nome)
    if not e:
        abort(404)
    if session.get("perfil") != "admin" and session.get("perfil") not in e["perfis"]:
        abort(403)
    return e


def _opcoes_campo(c):
    o = c.get("opcoes")
    if isinstance(o, str):
        return opts(o)
    return [(x, x) if isinstance(x, str) else x for x in (o or [])]


def _coletar(e, atual=None):
    vals, erros = {}, []
    for c in e["campos"]:
        n, t = c["nome"], c["tipo"]
        if t == "file":
            try:
                novo = salvar_upload(request.files.get(n))
            except ValueError as ex:
                erros.append(str(ex))
                novo = None
            vals[n] = novo or (atual[n] if atual else None)
            continue
        v = request.form.get(n, "").strip()
        if t == "password":
            if v:
                vals["senha_hash"] = generate_password_hash(v)
            elif not atual:
                erros.append("Informe uma senha.")
            continue
        if t == "number":
            v = int(v) if re.fullmatch(r"-?\d+", v) else None
        elif c.get("fk"):
            v = int(v) if v.isdigit() else None
        elif t == "datetime":
            v = v[:16]
        if c.get("req") and (v is None or v == ""):
            erros.append(f"Preencha o campo “{c['label']}”.")
        vals[n] = v
    return vals, erros


@app.route("/admin/<ent>/")
@login_required
def ent_lista(ent):
    e = _ent(ent)
    db = get_db()
    sql, params = e["sql"], []
    if "filtro" in e:
        chave, col = e["filtro"]
        v = request.args.get(chave, "")
        if v.isdigit():
            sql = sql.format(filtro=f"WHERE {col}=?")
            params = [int(v)]
        else:
            sql = sql.format(filtro="")
    linhas = [dict(r) for r in db.execute(sql, params)]
    return render_template("admin/lista.html", ent=ent, e=e, linhas=linhas,
                           filtro_sel=opts("selecoes") if "filtro" in e else None,
                           filtro_val=request.args.get("selecao", ""))


@app.route("/admin/<ent>/novo", methods=["GET", "POST"])
@app.route("/admin/<ent>/<int:eid>", methods=["GET", "POST"])
@login_required
def ent_form(ent, eid=None):
    e = _ent(ent)
    db = get_db()
    atual = None
    if eid:
        r = db.execute(f"SELECT * FROM {ent} WHERE id=?", (eid,)).fetchone()
        if not r:
            abort(404)
        atual = dict(r)
    if request.method == "POST":
        csrf_check()
        vals, erros = _coletar(e, atual)
        if ent == "jogos" and vals.get("sel_a") and vals.get("sel_a") == vals.get("sel_b"):
            erros.append("As duas seleções do jogo precisam ser diferentes.")
        if not erros:
            if e.get("ao_criar") and not atual:
                vals["criado"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            try:
                if atual:
                    sets = ", ".join(f"{k}=?" for k in vals)
                    db.execute(f"UPDATE {ent} SET {sets} WHERE id=?", [*vals.values(), eid])
                else:
                    cols = ", ".join(vals)
                    db.execute(f"INSERT INTO {ent}({cols}) VALUES({','.join('?' * len(vals))})",
                               list(vals.values()))
                bump(db)
                flash("Salvo com sucesso.", "ok")
                dest = request.args.get("voltar") or url_for("ent_lista", ent=ent)
                if ent == "jogadores" and not atual and "salvar_outro" in request.form:
                    dest = url_for("ent_form", ent=ent, selecao=vals.get("selecao_id"))
                return redirect(dest)
            except sqlite3.IntegrityError:
                erros.append("Já existe um registro com esse valor (ex.: nome de usuário repetido).")
        for m in erros:
            flash(m, "erro")
        atual = {**(atual or {}), **request.form.to_dict()}
    elif not atual:
        atual = {"selecao_id": request.args.get("selecao", ""), "cor": "#f5b800"}
        if ent == "jogos":
            atual.update(fase="grupos", status="agendado")
    campos = [dict(c, lista=_opcoes_campo(c)) if c["tipo"] == "select" else c for c in e["campos"]]
    return render_template("admin/form.html", ent=ent, e=e, campos=campos, v=atual, eid=eid)


@app.post("/admin/<ent>/<int:eid>/excluir")
@login_required
def ent_excluir(ent, eid):
    e = _ent(ent)
    csrf_check()
    db = get_db()
    if ent == "usuarios" and eid == session.get("uid"):
        flash("Você não pode excluir o próprio usuário.", "erro")
        return redirect(url_for("ent_lista", ent=ent))
    if ent == "usuarios" and db.execute("SELECT COUNT(*) FROM usuarios WHERE perfil='admin'").fetchone()[0] <= 1 \
            and db.execute("SELECT perfil FROM usuarios WHERE id=?", (eid,)).fetchone()["perfil"] == "admin":
        flash("É preciso manter ao menos um administrador.", "erro")
        return redirect(url_for("ent_lista", ent=ent))
    db.execute(f"DELETE FROM {ent} WHERE id=?", (eid,))
    bump(db)
    flash(f"{e['singular'].capitalize()} excluído(a).", "ok")
    return redirect(request.form.get("voltar") or url_for("ent_lista", ent=ent))


# ---------------------------------------------------------------- mata-mata
@app.post("/admin/jogos/gerar")
@role_required("admin")
def gerar_fase():
    csrf_check()
    db = get_db()
    destino = redirect(url_for("ent_lista", ent="jogos"))
    alvo = request.form.get("alvo")
    if alvo == "quartas":
        if db.execute("SELECT 1 FROM jogos WHERE fase<>'grupos'").fetchone():
            flash("O mata-mata já foi iniciado.", "erro")
            return destino
        pend = db.execute("SELECT COUNT(*) FROM jogos WHERE fase='grupos' AND status<>'encerrado'").fetchone()[0]
        if pend:
            flash(f"Ainda há {pend} jogo(s) da fase de grupos sem encerrar.", "erro")
            return destino
        grupos = calc_classificacao(db)
        if not all(k in grupos and len(grupos[k]) >= 2 for k in "ABCD"):
            flash("Para gerar as quartas é preciso ter os grupos A, B, C e D com ao menos 2 seleções.", "erro")
            return destino
        pares = [("A", "B"), ("C", "D"), ("B", "A"), ("D", "C")]
        for ga, gb in pares:
            db.execute("INSERT INTO jogos(fase, sel_a, sel_b, status) VALUES('quartas',?,?, 'agendado')",
                       (grupos[ga][0]["id"], grupos[gb][1]["id"]))
        flash("Quartas de final geradas (1ºA×2ºB, 1ºC×2ºD, 1ºB×2ºA, 1ºD×2ºC). Defina data e estádio.", "ok")
    elif alvo == "proxima":
        existentes = [f for f in ("quartas", "semi", "final") if db.execute(
            "SELECT 1 FROM jogos WHERE fase=?", (f,)).fetchone()]
        if not existentes:
            flash("Gere as quartas de final primeiro.", "erro")
            return destino
        atual = existentes[-1]
        if atual == "final":
            flash("A final já foi gerada.", "erro")
            return destino
        jg = listar_jogos(db, "j.fase=?", (atual,), order="ORDER BY j.id")
        ven = [vencedor(j) for j in jg]
        if any(v is None for v in ven):
            flash("Encerre todos os jogos da fase atual (com vencedor definido, usando pênaltis se preciso).", "erro")
            return destino
        if atual == "quartas":
            for i in range(0, len(ven) - 1, 2):
                db.execute("INSERT INTO jogos(fase, sel_a, sel_b) VALUES('semi',?,?)", (ven[i], ven[i + 1]))
            flash("Semifinais geradas.", "ok")
        else:
            db.execute("INSERT INTO jogos(fase, sel_a, sel_b) VALUES('final',?,?)", (ven[0], ven[1]))
            per = [perdedor(j) for j in jg]
            db.execute("INSERT INTO jogos(fase, sel_a, sel_b) VALUES('terceiro',?,?)", (per[0], per[1]))
            flash("Final e disputa do 3º lugar geradas.", "ok")
    bump(db)
    return destino


# ---------------------------------------------------------------- mesa do operador
@app.route("/admin/mesa/")
@role_required("operador")
def mesa_lista():
    db = get_db()
    return render_template("admin/mesa_lista.html",
                           ao_vivo=listar_jogos(db, "j.status='ao_vivo'"),
                           agendados=listar_jogos(db, "j.status IN ('agendado','adiado')"),
                           encerrados=listar_jogos(db, "j.status='encerrado'",
                                                   order="ORDER BY j.data_hora DESC", limit=10))


@app.route("/admin/mesa/<int:jid>")
@role_required("operador")
def mesa(jid):
    db = get_db()
    j = um_jogo(db, jid)
    if not j:
        abort(404)
    elenco = {}
    for chave, sid in (("a", j["sel_a"]), ("b", j["sel_b"])):
        elenco[chave] = [dict(r) for r in db.execute(
            "SELECT id, nome, numero FROM jogadores WHERE selecao_id=? ORDER BY COALESCE(numero,999), nome", (sid,))]
    eventos = [dict(r) for r in db.execute("SELECT * FROM eventos WHERE jogo_id=? ORDER BY id DESC", (jid,))]
    cfg_mesa = dict(jogo=jid, csrf=csrf_token(), dur=duracao(),
                    a=dict(id=j["sel_a"], nome=j["nome_a"], jogadores=elenco["a"]),
                    b=dict(id=j["sel_b"], nome=j["nome_b"], jogadores=elenco["b"]))
    return render_template("admin/mesa.html", j=j, eventos=eventos, cfg_mesa=cfg_mesa)


def _add_evento(db, jid, tipo, minuto, selecao_id=None, nome=None, nome2=None, jogador_id=None):
    cur = db.execute("""INSERT INTO eventos(jogo_id, tipo, minuto, selecao_id, jogador_id, jogador_nome,
                        jogador2_nome, texto, criado) VALUES(?,?,?,?,?,?,?,?,?)""",
                     (jid, tipo, minuto, selecao_id, jogador_id, nome, nome2, "", time.time()))
    return cur.lastrowid


@app.post("/admin/mesa/<int:jid>/acao")
@role_required("operador")
def mesa_acao(jid):
    csrf_check()
    db = get_db()
    j = um_jogo(db, jid)
    if not j:
        abort(404)
    d = request.get_json(silent=True) or {}
    acao = d.get("acao")
    ultimo_evento = db.execute("SELECT COALESCE(MAX(id),0) FROM eventos").fetchone()[0]
    now, dur = time.time(), duracao()
    placar = lambda jj: f"{jj['nome_a']} {jj['gols_a']} x {jj['gols_b']} {jj['nome_b']}"
    minuto = fmt_min(j, dur, now)
    mm = f" ({minuto})" if minuto else ""
    nomes = {j["sel_a"]: j["nome_a"], j["sel_b"]: j["nome_b"]}

    def sistema(tipo, texto):
        eid = _add_evento(db, jid, tipo, minuto)
        db.execute("UPDATE eventos SET texto=? WHERE id=?", (texto, eid))

    if acao == "iniciar":
        if j["status"] != "agendado" and j["status"] != "adiado":
            return jsonify(ok=False, erro="O jogo já foi iniciado."), 400
        db.execute("UPDATE jogos SET status='ao_vivo', periodo='1T', rel_base=0, rel_inicio=? WHERE id=?", (now, jid))
        sistema("inicio", f"Começou o jogo! {j['nome_a']} x {j['nome_b']}")
    elif acao == "intervalo":
        db.execute("UPDATE jogos SET periodo='intervalo', rel_base=?, rel_inicio=NULL WHERE id=?", (dur, jid))
        sistema("intervalo", f"Fim do 1º tempo. {placar(j)}")
    elif acao == "segundo_tempo":
        db.execute("UPDATE jogos SET periodo='2T', rel_base=?, rel_inicio=? WHERE id=?", (dur, now, jid))
        sistema("inicio2", f"Começou o 2º tempo! {placar(j)}")
    elif acao == "fim":
        db.execute("UPDATE jogos SET status='encerrado', periodo='fim', rel_base=?, rel_inicio=NULL WHERE id=?",
                   (total_min(j, now), jid))
        sistema("fim", f"Fim de jogo! {placar(j)}")
    elif acao == "reabrir":
        db.execute("UPDATE jogos SET status='ao_vivo', periodo='2T', rel_inicio=? WHERE id=?", (now, jid))
        sistema("lance", "Jogo reaberto pela organização.")
    elif acao == "pausar":
        if j["rel_inicio"]:
            db.execute("UPDATE jogos SET rel_base=?, rel_inicio=NULL WHERE id=?", (total_min(j, now), jid))
    elif acao == "retomar":
        if j["periodo"] in ("1T", "2T") and not j["rel_inicio"]:
            db.execute("UPDATE jogos SET rel_inicio=? WHERE id=?", (now, jid))
    elif acao == "ajustar_minuto":
        try:
            m = max(0, min(200, float(d.get("minuto"))))
        except (TypeError, ValueError):
            return jsonify(ok=False, erro="Minuto inválido."), 400
        db.execute("UPDATE jogos SET rel_base=?, rel_inicio=CASE WHEN rel_inicio IS NULL THEN NULL ELSE ? END WHERE id=?",
                   (m, now, jid))
    elif acao == "penaltis":
        try:
            pa, pb = int(d.get("pen_a")), int(d.get("pen_b"))
        except (TypeError, ValueError):
            return jsonify(ok=False, erro="Placar de pênaltis inválido."), 400
        db.execute("UPDATE jogos SET pen_a=?, pen_b=?, periodo=CASE WHEN status='ao_vivo' THEN 'penaltis' ELSE periodo END, "
                   "rel_base=CASE WHEN status='ao_vivo' THEN ? ELSE rel_base END, "
                   "rel_inicio=CASE WHEN status='ao_vivo' THEN NULL ELSE rel_inicio END WHERE id=?",
                   (pa, pb, total_min(j, now), jid))
        sistema("lance", f"Pênaltis: {j['nome_a']} {pa} x {pb} {j['nome_b']}")
    elif acao in ("gol", "gol_contra", "amarelo", "vermelho"):
        sid = d.get("selecao_id")
        if sid not in nomes:
            return jsonify(ok=False, erro="Seleção inválida."), 400
        nome, pid = (d.get("jogador_nome") or "").strip() or None, d.get("jogador_id")
        if pid:
            r = db.execute("SELECT nome, numero FROM jogadores WHERE id=? AND selecao_id=?", (pid, sid)).fetchone()
            if r:
                nome = r["nome"]
            else:
                pid = None
        eid = _add_evento(db, jid, acao, minuto, sid, nome, None, pid)
        quem = f" — {nome}" if nome else ""
        if acao in ("gol", "gol_contra"):
            recalc_placar(db, jid)
            j2 = um_jogo(db, jid)
            if acao == "gol":
                txt = f"GOL! {placar(j2)}{quem}{mm}"
            else:
                txt = f"Gol contra! {placar(j2)}{quem}{mm}"
        elif acao == "amarelo":
            txt = f"Cartão amarelo: {nomes[sid]}{quem}{mm}"
        else:
            txt = f"Cartão vermelho: {nomes[sid]}{quem}{mm}"
        db.execute("UPDATE eventos SET texto=? WHERE id=?", (txt, eid))
    elif acao == "sub":
        sid = d.get("selecao_id")
        if sid not in nomes:
            return jsonify(ok=False, erro="Seleção inválida."), 400
        entra, sai = (d.get("entra") or "").strip(), (d.get("sai") or "").strip()
        eid = _add_evento(db, jid, "sub", minuto, sid, entra or None, sai or None)
        db.execute("UPDATE eventos SET texto=? WHERE id=?",
                   (f"Substituição {nomes[sid]}: entra {entra or '—'}, sai {sai or '—'}{mm}", eid))
    elif acao == "lance":
        t = (d.get("texto") or "").strip()[:240]
        if not t:
            return jsonify(ok=False, erro="Escreva o lance."), 400
        sistema("lance", f"{t}{mm}")
    elif acao == "excluir_evento":
        ev = db.execute("SELECT * FROM eventos WHERE id=? AND jogo_id=?", (d.get("evento"), jid)).fetchone()
        if ev:
            db.execute("DELETE FROM eventos WHERE id=?", (ev["id"],))
            recalc_placar(db, jid)
    else:
        return jsonify(ok=False, erro="Ação desconhecida."), 400
    bump(db)
    novos = [dict(r) for r in db.execute(
        "SELECT e.id, e.jogo_id, e.tipo, e.texto, j.sel_a, j.sel_b FROM eventos e JOIN jogos j ON j.id=e.jogo_id "
        "WHERE e.id>? AND e.jogo_id=? ORDER BY e.id", (ultimo_evento, jid)) if r["tipo"] in NOTIFICAVEIS]
    if novos:
        threading.Thread(target=push_eventos, args=(novos,), daemon=True).start()
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True,
            debug=bool(os.environ.get("DEBUG")))
