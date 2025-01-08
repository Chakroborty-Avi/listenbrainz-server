from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import current_user
from sqlalchemy import text
import psycopg2
from collections import defaultdict
from psycopg2.extras import DictCursor

from brainzutils import cache

from listenbrainz.db.similar_users import get_top_similar_users
from listenbrainz.db.genre import load_genre_with_subgenres
from listenbrainz.webserver import db_conn, ts_conn
from listenbrainz.webserver.views.user import TAG_HEIRARCHY_CACHE_KEY, TAG_HEIRARCHY_CACHE_EXPIRY

explore_bp = Blueprint('explore', __name__)


@explore_bp.route("/similar-users/", methods=['POST'])
def similar_users():
    """ Show all of the users with the highest similarity in order to make
        them visible to all of our users. This view can show bugs in the algorithm
        and spammers as well.
    """

    similar_users = get_top_similar_users(db_conn)

    return jsonify({
        "similarUsers": similar_users
    })


@explore_bp.route("/music-neighborhood/", methods=['POST'])
def artist_similarity():
    """ Explore artist similarity """

    result = ts_conn.execute(text("""
         SELECT artist_mbid::TEXT
           FROM popularity.artist
       ORDER BY total_listen_count DESC
          LIMIT 1
     """))

    artist_mbid = result.fetchone()[0]
    data = {
        "algorithm": "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30",
        "artist_mbid": artist_mbid
    }

    return jsonify(data)


@explore_bp.route("/ai-brainz/")
def ai_brainz():
    """ Explore your love of Rick """

    return render_template("index.html")


def process_genre_explorer_data(data: list, genre_mbid: str) -> tuple[dict, list[dict], dict, list[dict]]:
    adj_matrix = defaultdict(list)
    id_name_map = {}
    parent_map = defaultdict(set)

    # Build the graph
    for row in data:
        genre_id = row["genre_gid"]
        id_name_map[genre_id] = row.get("genre")

        subgenre_id = row["subgenre_gid"]
        if subgenre_id:
            id_name_map[subgenre_id] = row.get("subgenre")
            if subgenre_id not in parent_map:
                parent_map[subgenre_id] = set()
            parent_map[subgenre_id].add(genre_id)
            adj_matrix[genre_id].append(subgenre_id)
        else:
            adj_matrix[genre_id] = []
            parent_map[genre_id] = set()
    
    if genre_mbid not in id_name_map:
        return None, None, None, None

    # 1. Current genre
    current_genre = {"mbid": genre_mbid, "name": id_name_map[genre_mbid]}

    # 2. All children of the genre
    children = adj_matrix[genre_mbid]

    # 3. Parent graph data
    parent_nodes = set()
    parent_edges = set()

    def collect_parents(genre):
        """Recursively collect all parents and their relationships"""
        if genre in parent_nodes:
            return

        parent_nodes.add(genre)
        for parent in parent_map[genre]:
            parent_edges.add((parent, genre))
            collect_parents(parent)

    # Start collection from our target genre
    collect_parents(genre_mbid)

    # 4. All siblings of the genre
    siblings = set()
    for parent in parent_map[genre_mbid]:
        siblings.update(adj_matrix[parent])
    siblings.discard(genre_mbid)

    def format_genre_list(genre_list: list) -> list:
        return [{"mbid": genre, "name": id_name_map[genre]} for genre in genre_list]

    # Format parent graph data
    parent_graph = {
        "nodes": format_genre_list(parent_nodes),
        "edges": [{"source": source, "target": target} for source, target in parent_edges]
    }

    return current_genre, \
        format_genre_list(children), \
        parent_graph, \
        format_genre_list(siblings)


@explore_bp.route("/genre-explorer/", methods=["POST"])
def genre_explorer():
    """ Get genre explorer data """
    # Get genre-mbid from request
    genre_mbid = request.args.get("genre", "")
    if not genre_mbid:
        return jsonify({"error": "genre parameter is required"}), 400

    try:
        data = cache.get(TAG_HEIRARCHY_CACHE_KEY)
        if not data:
            with psycopg2.connect(current_app.config["MB_DATABASE_URI"]) as mb_conn,\
                    mb_conn.cursor(cursor_factory=DictCursor) as mb_curs:
                data = load_genre_with_subgenres(mb_curs)
                data = [dict(row) for row in data] if data else []
            cache.set(TAG_HEIRARCHY_CACHE_KEY, data, expirein=TAG_HEIRARCHY_CACHE_EXPIRY)
    except Exception as e:
        current_app.logger.error("Error loading genre explorer data: %s", e)
        return jsonify({"error": "Failed to load genre explorer data"}), 500

    genre, children, parents, siblings = process_genre_explorer_data(data, genre_mbid)
    if not genre:
        return jsonify({"error": "Genre not found"}), 404

    return jsonify({
        "children": children,
        "parents": parents,
        "siblings": siblings,
        "genre": genre
    })


@explore_bp.route("/lb-radio/", methods=["POST"])
def lb_radio():
    """ LB Radio view

        Possible page arguments:
           mode: string, must be easy, medium or hard.
           prompt: string, the prompt for playlist generation.
    """

    mode = request.args.get("mode", "")
    if mode != "" and mode not in ("easy", "medium", "hard"):
        return jsonify({"error": "mode parameter is required and must be one of 'easy', 'medium' or 'hard'."}), 400

    prompt = request.args.get("prompt", "")
    if prompt != "" and prompt == "":
        return jsonify({"error": "prompt parameter is required and must be non-zero length."}), 400

    if current_user.is_authenticated:
        user = current_user.musicbrainz_id
        token = current_user.auth_token
    else:
        user = ""
        token = ""
    data = {
        "mode": mode,
        "prompt": prompt,
        "user": user,
        "token": token
    }

    return jsonify(data)


@explore_bp.route('/', defaults={'path': ''})
@explore_bp.route('/<path:path>/')
def index(path):
    """ Main explore page for users to browse the various explore features """

    return render_template("index.html")
