# app.py
from flask import Flask, render_template
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from pathlib import Path

app = Flask(__name__)

DB_PATH = Path(__file__).parent.parent / "bot" / "fantasy.db"
engine  = create_engine(f"sqlite:///{DB_PATH}")
Session = sessionmaker(bind=engine)

def get_db():
    return Session()

# ── Home ──────────────────────────────────────────────────────────
@app.route("/")
def index():
    db = get_db()
    try:
        games    = db.execute(text("SELECT * FROM games WHERE is_final=1 ORDER BY id DESC LIMIT 5")).fetchall()
        managers = db.execute(text("SELECT * FROM managers")).fetchall()
        return render_template("index.html", games=games, managers=managers)
    finally:
        db.close()

# ── Standings — W/D/L from fantasy_matchups ───────────────────────
@app.route("/standings")
def standings():
    db = get_db()
    try:
        managers = db.execute(text("""
            SELECT
                m.*,
                COALESCE(SUM(CASE
                    WHEN fm.winner_id = m.id THEN 3
                    WHEN fm.winner_id IS NULL
                     AND (fm.home_manager_id=m.id OR fm.away_manager_id=m.id) THEN 1
                    ELSE 0 END),0) AS league_points,
                COALESCE(SUM(CASE WHEN fm.winner_id=m.id THEN 1 ELSE 0 END),0) AS wins,
                COALESCE(SUM(CASE
                    WHEN fm.winner_id IS NULL
                     AND (fm.home_manager_id=m.id OR fm.away_manager_id=m.id) THEN 1
                    ELSE 0 END),0) AS draws,
                COALESCE(SUM(CASE
                    WHEN fm.winner_id != m.id
                     AND fm.winner_id IS NOT NULL
                     AND (fm.home_manager_id=m.id OR fm.away_manager_id=m.id) THEN 1
                    ELSE 0 END),0) AS losses,
                COALESCE(SUM(CASE
                    WHEN fm.home_manager_id=m.id THEN fm.home_points
                    WHEN fm.away_manager_id=m.id THEN fm.away_points
                    ELSE 0 END),0) AS total_fantasy_pts
            FROM managers m
            LEFT JOIN fantasy_matchups fm
                ON fm.home_manager_id=m.id OR fm.away_manager_id=m.id
            GROUP BY m.id
            ORDER BY league_points DESC, total_fantasy_pts DESC
        """)).fetchall()
        return render_template("standings.html", managers=managers)
    finally:
        db.close()

# ── Teams ─────────────────────────────────────────────────────────
@app.route("/teams")
def teams():
    db = get_db()
    try:
        managers = db.execute(text("SELECT * FROM managers")).fetchall()
        return render_template("teams.html", managers=managers)
    finally:
        db.close()

@app.route("/teams/<int:manager_id>")
def team_detail(manager_id):
    db = get_db()
    try:
        manager = db.execute(
            text("SELECT * FROM managers WHERE id=:id"), {"id": manager_id}
        ).fetchone()

        # get all available gameweeks
        gw_rows = db.execute(text(
            "SELECT DISTINCT gameweek FROM rosters WHERE manager_id=:id ORDER BY gameweek"
        ), {"id": manager_id}).fetchall()
        gameweeks = [r.gameweek for r in gw_rows]

        # default to highest (most recent) gameweek
        from flask import request
        try:
            selected_gw = int(request.args.get("gw", max(gameweeks) if gameweeks else 1))
        except (ValueError, TypeError):
            selected_gw = max(gameweeks) if gameweeks else 1

        # get game IDs for selected gameweek
        game_ids = db.execute(text("""
            SELECT g.id FROM games g
            JOIN gameweeks gw ON g.gameweek_id = gw.id
            WHERE gw.number = :gw AND g.is_final = 1
        """), {"gw": selected_gw}).fetchall()
        gids = [r.id for r in game_ids]

        # fetch roster with fantasy points for the selected gameweek
        if gids:
            placeholders = ",".join(str(g) for g in gids)
            roster = db.execute(text(f"""
                SELECT r.*, p.name, p.role, p.team AS club,
                       COALESCE(SUM(ps.fantasy_points), 0) AS gw_pts
                FROM rosters r
                JOIN players p ON r.player_id = p.id
                LEFT JOIN player_stats ps
                    ON ps.player_id = r.player_id
                    AND ps.match_id IN ({placeholders})
                WHERE r.manager_id = :id AND r.gameweek = :gw
                GROUP BY r.id, p.id
                ORDER BY r.is_starter DESC, p.role
            """), {"id": manager_id, "gw": selected_gw}).fetchall()
        else:
            roster = db.execute(text("""
                SELECT r.*, p.name, p.role, p.team AS club, 0 AS gw_pts
                FROM rosters r
                JOIN players p ON r.player_id = p.id
                WHERE r.manager_id = :id AND r.gameweek = :gw
                ORDER BY r.is_starter DESC, p.role
            """), {"id": manager_id, "gw": selected_gw}).fetchall()

        starters  = [r for r in roster if r.is_starter]
        bench     = [r for r in roster if not r.is_starter]
        gw_total  = sum(r.gw_pts for r in starters)

        return render_template("team_detail.html",
                               manager=manager,
                               starters=starters,
                               bench=bench,
                               gameweeks=gameweeks,
                               selected_gw=selected_gw,
                               gw_total=gw_total)
    finally:
        db.close()

# ── Players ───────────────────────────────────────────────────────
@app.route("/players")
def players():
    db = get_db()
    try:
        players = db.execute(text("SELECT * FROM players ORDER BY team, role")).fetchall()
        return render_template("players.html", players=players)
    finally:
        db.close()

# ── Real matches ──────────────────────────────────────────────────
@app.route("/matches")
def matches():
    db = get_db()
    try:
        games = db.execute(text("SELECT * FROM games ORDER BY week, id")).fetchall()
        matchups = db.execute(text("""
            SELECT fm.*,
                hm.team_name AS home_team_name, hm.logo_url AS home_logo,
                am.team_name AS away_team_name, am.logo_url AS away_logo,
                wm.team_name AS winner_name
            FROM fantasy_matchups fm
            JOIN managers hm ON fm.home_manager_id=hm.id
            JOIN managers am ON fm.away_manager_id=am.id
            LEFT JOIN managers wm ON fm.winner_id=wm.id
            ORDER BY fm.gameweek, fm.id
        """)).fetchall()
        return render_template("matches.html", games=games, matchups=matchups)
    finally:
        db.close()

@app.route("/matches/<int:game_id>")
def match_detail(game_id):
    db = get_db()
    try:
        game = db.execute(
            text("SELECT * FROM games WHERE id=:id"), {"id": game_id}
        ).fetchone()
        stats = db.execute(text("""
            SELECT ps.*, p.name, p.team, p.role
            FROM player_stats ps
            JOIN players p ON ps.player_id=p.id
            WHERE ps.match_id=:id
            ORDER BY ps.fantasy_points DESC
        """), {"id": game_id}).fetchall()
        home_stats = [s for s in stats if s.team == game.home]
        away_stats = [s for s in stats if s.team == game.away]
        return render_template("match_detail.html",
                               game=game, home_stats=home_stats, away_stats=away_stats)
    finally:
        db.close()

# ── Fantasy matchup detail ─────────────────────────────────────────
@app.route("/fantasy/<int:matchup_id>")
def fantasy_matchup(matchup_id):
    db = get_db()
    try:
        matchup = db.execute(text("""
            SELECT fm.*,
                hm.team_name AS home_team_name, hm.logo_url AS home_logo,
                am.team_name AS away_team_name, am.logo_url AS away_logo,
                wm.team_name AS winner_name
            FROM fantasy_matchups fm
            JOIN managers hm ON fm.home_manager_id=hm.id
            JOIN managers am ON fm.away_manager_id=am.id
            LEFT JOIN managers wm ON fm.winner_id=wm.id
            WHERE fm.id=:id
        """), {"id": matchup_id}).fetchone()

        # get game IDs for this gameweek
        game_ids = db.execute(text("""
            SELECT g.id FROM games g
            JOIN gameweeks gw ON g.gameweek_id=gw.id
            WHERE gw.number=:gw AND g.is_final=1
        """), {"gw": matchup.gameweek}).fetchall()
        gids = [r.id for r in game_ids]

        def get_player_pts(manager_id):
            if not gids:
                return []
            placeholders = ",".join(str(g) for g in gids)
            return db.execute(text(f"""
                SELECT p.name, p.role,
                       COALESCE(SUM(ps.fantasy_points),0) AS total_pts
                FROM rosters r
                JOIN players p ON r.player_id=p.id
                LEFT JOIN player_stats ps
                    ON ps.player_id=r.player_id
                    AND ps.match_id IN ({placeholders})
                WHERE r.manager_id=:mid AND r.gameweek=:gw AND r.is_starter=1
                GROUP BY p.id, p.name, p.role
                ORDER BY total_pts DESC
            """), {"mid": manager_id, "gw": matchup.gameweek}).fetchall()

        home_players = get_player_pts(matchup.home_manager_id)
        away_players = get_player_pts(matchup.away_manager_id)

        return render_template("fantasy_matchup.html",
                               matchup=matchup,
                               home_players=home_players,
                               away_players=away_players)
    finally:
        db.close()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)