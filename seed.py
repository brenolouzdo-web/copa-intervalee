# -*- coding: utf-8 -*-
"""
Dados de DEMONSTRAÇÃO (fictícios) para testar a plataforma.

  python seed.py            -> carrega seleções, jogos, resultados e um jogo "ao vivo" de exemplo
  python seed.py --limpar   -> apaga TODOS os dados de campeonato (mantém usuários e configurações)
  python seed.py --selecoes -> apaga e cadastra SÓ as seleções confirmadas (sem jogos/atletas) para começar valendo

Os resultados, gols e nomes de atletas são inventados — nada aqui é informação oficial.
"""
import random
import sys
import time
from datetime import datetime, timedelta

from app import conectar, bump, init_db, recalc_placar

SELECOES = [  # nome, sigla, cidade, região, grupo, cor
    ("Ibirataia", "IBI", "Ibirataia", "Médio Rio de Contas", "A", "#1e66d0"),
    ("Laje", "LAJ", "Laje", "Vale do Jiquiriçá", "A", "#d02828"),
    ("Varzedo", "VAR", "Varzedo", "Recôncavo Baiano", "A", "#1f8f4a"),
    ("Jequié", "JEQ", "Jequié", "Médio Rio de Contas", "A", "#e0a800"),
    ("Ichu", "ICH", "Ichu", "Sisal", "B", "#2e8b57"),
    ("Gandu", "GAN", "Gandu", "Baixo Sul", "B", "#c0392b"),
    ("Teolândia", "TEO", "Teolândia", "Baixo Sul", "B", "#8e44ad"),
    ("Maracás", "MAR", "Maracás", "Vale do Jiquiriçá", "B", "#16a085"),
    ("Itaeté", "ITA", "Itaeté", "Chapada Diamantina", "C", "#e67e22"),
    ("Itagi", "ITG", "Itagi", "Médio Rio de Contas", "C", "#2980b9"),
    ("Santo Antônio de Jesus", "SAJ", "Santo Antônio de Jesus", "Recôncavo Baiano", "C", "#d35400"),
    ("Cruz das Almas", "CRU", "Cruz das Almas", "Recôncavo Baiano", "C", "#27ae60"),
    ("Brejões", "BRE", "Brejões", "Vale do Jiquiriçá", "D", "#7f8c8d"),
    ("Manoel Vitorino", "MVT", "Manoel Vitorino", "Médio Rio de Contas", "D", "#c0392b"),
    ("Poções", "POC", "Poções", "Sudoeste da Bahia", "D", "#2c3e50"),
    ("Lauro de Freitas", "LDF", "Lauro de Freitas", "Metropolitana", "D", "#f39c12"),
]
NOMES = ["Carlos", "José", "Antônio", "Marcos", "Paulo", "Roberto", "Luiz", "Edson", "Sérgio",
         "Jorge", "Raimundo", "Adriano", "Gilson", "Valdir", "Nelson", "Cláudio", "Osvaldo", "Reinaldo"]
SOBRENOMES = ["Silva", "Santos", "Oliveira", "Souza", "Pereira", "Costa", "Rodrigues", "Almeida",
              "Nascimento", "Lima", "Araújo", "Ribeiro", "Carvalho", "Gomes", "Barbosa", "Cardoso"]
POS = ["Goleiro"] + ["Zagueiro"] * 3 + ["Lateral"] * 2 + ["Volante"] * 2 + ["Meia"] * 3 + ["Atacante"] * 4


# Seleções confirmadas segundo os cards do Instagram @copaintervale (nome, região). Confira e ajuste no painel.
CONFIRMADAS = [
    ("Jequié", "Médio Rio de Contas"), ("Boa Nova", "Médio Rio de Contas"), ("Laje", "Vale do Jiquiriçá"),
    ("Gandu", "Baixo Sul"), ("Lauro de Freitas", "Metropolitana"), ("Ichu", "Sisal"),
    ("Tiruçu", "Vale do Jiquiriçá"), ("Lajedo do Tabocal", "Vale do Jiquiriçá"),
    ("Ibirataia", "Médio Rio de Contas"), ("Jiquiriçá", "Vale do Jiquiriçá"), ("Piraí do Norte", "Baixo Sul"),
    ("Conceição do Almeida", "Recôncavo Baiano"), ("Brejões", "Vale do Jiquiriçá"),
    ("Cruz das Almas", "Recôncavo Baiano"), ("Santo Antônio de Jesus", "Recôncavo Baiano"),
    ("Varzedo", "Recôncavo Baiano"), ("Teolândia", "Baixo Sul"), ("KM-70", "Vale do Jiquiriçá"),
    ("Maracás", "Vale do Jiquiriçá"), ("Irajuba", "Vale do Jiquiriçá"), ("Santa Inês", "Vale do Jiquiriçá"),
    ("Flu de 97 (Feira de Santana)", "Portal do Sertão"), ("Andaraí", "Chapada Diamantina"),
    ("Itaetê", "Chapada Diamantina"), ("Iaçu", "Chapada Diamantina"), ("Poções", "Sudoeste da Bahia"),
    ("Maraú", "Península de Maraú"), ("Manoel Vitorino", "Médio Rio de Contas"), ("Itagi", "Médio Rio de Contas"),
]


def selecoes_confirmadas(db):
    limpar(db)
    for nome, regiao in CONFIRMADAS:
        sigla = "".join(c for c in nome.upper().replace("-", "") if c.isalpha())[:3]
        db.execute("INSERT INTO selecoes(nome, sigla, cidade, regiao, cor) VALUES(?,?,?,?,?)",
                   (nome, sigla, nome.split(" (")[0], regiao, "#f5b800"))
    db.commit()
    bump(db)


def limpar(db):
    for t in ("eventos", "jogos", "jogadores", "selecoes", "estadios", "arbitros",
              "noticias", "midia", "patrocinadores"):
        db.execute(f"DELETE FROM {t}")
    db.commit()


def demo(db):
    rnd = random.Random(2026)
    limpar(db)
    est = []
    for n, c, cap in [("Estádio Municipal (demonstração)", "Ibirataia", 3000),
                      ("Estádio Ruy José de Almeida (demonstração)", "Laje", 5000)]:
        est.append(db.execute("INSERT INTO estadios(nome, cidade, capacidade) VALUES(?,?,?)", (n, c, cap)).lastrowid)
    arb = [db.execute("INSERT INTO arbitros(nome, federacao) VALUES(?,?)", (n, "Quadro de demonstração")).lastrowid
           for n in ("Árbitro Exemplo 1", "Árbitro Exemplo 2", "Árbitro Exemplo 3", "Árbitro Exemplo 4")]
    ids, jogs = {}, {}
    for nome, sig, cid, reg, grp, cor in SELECOES:
        sid = db.execute("INSERT INTO selecoes(nome, sigla, cidade, regiao, grupo, cor, tecnico, historico) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         (nome, sig, cid, reg, grp, cor, "Técnico (a cadastrar)",
                          "Histórico da seleção — edite no painel.")).lastrowid
        ids[nome] = (sid, grp)
        jogs[sid] = []
        usados = set()
        for num in range(1, 19):
            while True:
                nm = f"{rnd.choice(NOMES)} {rnd.choice(SOBRENOMES)}"
                if nm not in usados:
                    usados.add(nm)
                    break
            jid = db.execute("INSERT INTO jogadores(selecao_id, nome, numero, posicao) VALUES(?,?,?,?)",
                             (sid, nm, num, POS[(num - 1) % len(POS)])).lastrowid
            jogs[sid].append((jid, nm, POS[(num - 1) % len(POS)]))

    agora = datetime.now().replace(minute=0, second=0, microsecond=0)
    # fase de grupos: 3 rodadas por grupo (todos contra todos)
    por_grupo = {}
    for nome, (sid, grp) in ids.items():
        por_grupo.setdefault(grp, []).append(sid)
    rodadas = [[(0, 1), (2, 3)], [(0, 2), (1, 3)], [(0, 3), (1, 2)]]
    jogo_live = None
    k = 0
    for r, pares in enumerate(rodadas, 1):
        for grp in sorted(por_grupo):
            for i, j in pares:
                a, b = por_grupo[grp][i], por_grupo[grp][j]
                dia = agora - timedelta(days=(3 - r) * 7 + 1) + timedelta(hours=k % 4 * 2)
                if r == 3 and grp == "A" and (i, j) == (0, 3):
                    dia = agora  # jogo "ao vivo" de demonstração
                elif r == 3:
                    dia = agora + timedelta(days=1 + k % 3, hours=k % 4 * 2)
                status = "encerrado" if r < 3 else "agendado"
                jid = db.execute(
                    "INSERT INTO jogos(fase, grupo, rodada, sel_a, sel_b, data_hora, estadio_id, arbitro_id, aux1_id, aux2_id, status, periodo) "
                    "VALUES('grupos',?,?,?,?,?,?,?,?,?,?,?)",
                    (grp, r, a, b, dia.strftime("%Y-%m-%dT%H:%M"), est[k % 2], arb[0], arb[1], arb[2],
                     status, "fim" if status == "encerrado" else "pre")).lastrowid
                if status == "encerrado":
                    gols(db, rnd, jid, a, b, jogs, final=True)
                if r == 3 and grp == "A" and (i, j) == (0, 3):
                    jogo_live = (jid, a, b)
                k += 1
    # jogo ao vivo de demonstração
    if jogo_live:
        jid, a, b = jogo_live
        db.execute("UPDATE jogos SET status='ao_vivo', periodo='2T', rel_base=?, rel_inicio=?, data_hora=? WHERE id=?",
                   (38.5, time.time(), (agora - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M"), jid))
        gols(db, rnd, jid, a, b, jogs, final=False)

    db.execute("INSERT INTO noticias(titulo, resumo, texto, criado) VALUES(?,?,?,?)",
               ("Plataforma em tempo real no ar (exemplo)",
                "Notícia de demonstração. Apague e publique as suas no painel.",
                "Este texto é só um exemplo. Entre no painel › Notícias para publicar conteúdo oficial.",
                datetime.now().strftime("%Y-%m-%d %H:%M")))
    for i, (n, nv) in enumerate([("Seu logo aqui — Master", "Master"), ("Seu logo aqui — Ouro", "Ouro"),
                                 ("Seu logo aqui — Prata", "Prata"), ("Seu logo aqui — Apoio", "Apoio")]):
        db.execute("INSERT INTO patrocinadores(nome, nivel, ordem) VALUES(?,?,?)", (n, nv, i))
    db.commit()
    bump(db)


def gols(db, rnd, jid, a, b, jogs, final):
    ga, gb = rnd.choice([0, 0, 1, 1, 1, 2, 2, 3]), rnd.choice([0, 0, 1, 1, 2, 2, 3])
    if not final:
        ga, gb = 1, 1
    lista = [(a, "a")] * ga + [(b, "b")] * gb
    minutos = sorted(rnd.sample(range(2, 58 if final else 38), len(lista)))
    for (sid, _), m in zip(lista, minutos):
        pid, nm, _p = rnd.choice([x for x in jogs[sid] if x[2] in ("Atacante", "Meia", "Atacante")])
        txt = f"GOL! — {nm} ({m}')"
        db.execute("INSERT INTO eventos(jogo_id, tipo, minuto, selecao_id, jogador_id, jogador_nome, texto, criado) "
                   "VALUES(?,?,?,?,?,?,?,?)", (jid, "gol", f"{m}'", sid, pid, nm, txt, time.time()))
    for _ in range(rnd.randint(0, 3)):
        sid = rnd.choice([a, b])
        pid, nm, _p = rnd.choice(jogs[sid])
        tipo = "amarelo" if rnd.random() > 0.1 else "vermelho"
        db.execute("INSERT INTO eventos(jogo_id, tipo, minuto, selecao_id, jogador_id, jogador_nome, texto, criado) "
                   "VALUES(?,?,?,?,?,?,?,?)", (jid, tipo, f"{rnd.randint(5, 55)}'", sid, pid, nm,
                                              f"Cartão {tipo}: {nm}", time.time()))
    recalc_placar(db, jid)


if __name__ == "__main__":
    init_db()
    c = conectar()
    if "--selecoes" in sys.argv:
        selecoes_confirmadas(c)
        print(f"{len(CONFIRMADAS)} seleções confirmadas cadastradas (sem grupos). Defina grupos, escudos e jogos no painel.")
    elif "--limpar" in sys.argv:
        limpar(c)
        bump(c)
        print("Dados do campeonato apagados. Cadastre as seleções reais pelo painel.")
    else:
        demo(c)
        print("Dados de demonstração carregados (fictícios). Use --limpar para começar do zero.")
    c.close()
