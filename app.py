#!/usr/bin/python3

from flask import (
    Flask,
    render_template,
    render_template_string,
    send_file,
    redirect,
    request,
    send_from_directory,
    Response,
    jsonify,
)
import re, os, time, sys
import natsort
from urllib.parse import unquote, quote
import posixpath, json
from math import ceil, floor
from random import sample, randint, random
from threading import Thread
import argparse
import requests
import signal
import traceback

import config

parser = argparse.ArgumentParser(
    description="My Timeline - A personal social media archive and viewer."
)

parser.add_argument(
    "--debug", action="store_true", help="Include this option to enable debug mode."
)

parser.add_argument("--skip-scan", action="store_true", help="Skip startup scan.")
parser.add_argument(
    "--update-daemon",
    action="store_true",
    help="Regularly update, from oldest to recent updated users, DO NOT use if you have a large number of users or posts. Recommended to run once and then disable.",
)
parser.add_argument(
    "-c",
    "--config",
    type=str,
    default="config.json",
    help="Path to config file. See example_config.jsonc for reference.",
)
parser.add_argument(
    "-v", "--verbose", type=int, default=0, help="Verbose level for logging, 0-3."
)
parser.add_argument(
    "--monitor-timeline",
    action="store_true",
    help="Regularly monitor timelines for new posts and automatically download them. Recommended to run once and then disable. Bluesky only, and fill in 'bsky_auth.json' first.",
)

args, unknown = parser.parse_known_args()
args.debug = bool(args.debug)
args.verbose = int(args.verbose)

if "gunicorn" in sys.argv[0]:
    print("Running with Gunicorn, forcing debug mode off and verbose level to 0.")
    args.debug = False
    args.verbose = 0
    print(
        "To avoid config argument conflict with Gunicorn, using `config.json` as default config file, your specified config file will be loaded again very soon in wsgi_app()."
    )
    args.config = "config.json"

print("Starting MT with options:", args)
config.read_config(args.config)

import backend, utils, logger, run_command, live_timeline_monitor

logger.VERBOSE_LEVEL = args.verbose
backend.debug_mode = args.debug
if args.debug:
    logger.VERBOSE_LEVEL = max(logger.VERBOSE_LEVEL, 1)
    logger.log("Debug mode enabled.", verbose=1)
args.skip_scan = bool(args.skip_scan)
args.monitor_timeline = bool(args.monitor_timeline)


def set_cache_header(response):
    if not args.debug:
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response


def check_auth(required_role=utils.ROLE_AUTHORIZED):
    def decorator(func):
        def auth_wrapper(*args, **kwargs):
            session_key = request.cookies.get("session_key", "")
            super_session_key = request.headers.get("X-Session-Key", "")
            role = utils.get_role(session_key, super_session_key)
            if role >= required_role or config.no_auth:
                return func(*args, **kwargs)
            else:
                if role == utils.ROLE_UNAUTHORIZED:
                    logger.log(
                        f"Unauthorized access attempt to {func.__name__} by user with role {role}",
                        type="warning",
                    )
                    return redirect(posixpath.join("/", config.url_base, "login"))
                elif role == utils.ROLE_AUTHORIZED:
                    logger.log(
                        f"Forbidden access attempt to {func.__name__} by user with role {role}",
                        type="warning",
                    )
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Forbidden: You don't have permission to access this resource.",
                            }
                        ),
                        403,
                    )
                else:
                    logger.log(
                        f"Access attempt to {func.__name__} by user with unknown role {role}",
                        type="warning",
                    )
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": "Forbidden: Your role is not recognized.",
                            }
                        ),
                        403,
                    )

        auth_wrapper.__name__ = func.__name__
        return auth_wrapper

    return decorator


tl_current_sort = "new"
tl_current_page = {
    "new": 0,
    "top": 0,
    "random": 0,
}


def get_posts(method="tl", query="", sort_type="new", page=0, user_name="", type_=""):
    """
    A helper function to get posts for timeline.
    :param method: "tl", "fav", or "user"
    :param query: search query
    :param sort_type: "new", "top", or "random" (only used for "tl" method)
    :param page: page number (0-indexed)
    :param user_name: user name (only used for "user" method)
    :param type_: user type like "x", "bsky", "reddit", "fa" (only used for "user" method)

    :return: tuple of (list of post IDs for the page, total post count)
    """
    global tl_current_sort, tl_current_page

    if method == "tl":
        if query:
            sorted_posts_id, all_post_count = backend.db.query_post_by_text(
                query,
                page * config.items_per_page,
                config.items_per_page,
                sort_type=sort_type,
            )
            sorted_posts_id = [i[0] for i in sorted_posts_id][::-1]
        else:
            all_post_count = backend.db.get_post_count()
            if sort_type == "new":
                sorted_posts_id = backend.db.get_new(
                    page * config.items_per_page, config.items_per_page
                )
            elif sort_type == "top":
                sorted_posts_id = backend.db.get_top(
                    page * config.items_per_page, config.items_per_page
                )
            elif sort_type == "random":
                sorted_posts_id = backend.db.get_random(config.items_per_page)
            else:
                return [], 0
            sorted_posts_id = [i[0] for i in sorted_posts_id][::-1]
        return sorted_posts_id, all_post_count

    elif method == "fav":
        sorted_posts_id = backend.get_fav()
        all_post_count = len(sorted_posts_id)
        sorted_posts_id = sorted_posts_id[
            page * config.items_per_page : (page + 1) * config.items_per_page
        ][::-1]
        sorted_posts_id = [i[0] for i in sorted_posts_id if i[0]]
        return sorted_posts_id, all_post_count

    elif method == "user":
        if not user_name or not type_:
            return [], 0
        uid = f"{user_name}@{type_}"
        all_rows = backend.db.query_rows(
            selected_table="posts",
            key="uid",
            value=uid,
            sort_key=utils.sort_keys[sort_type],
        )
        all_post_count = len(all_rows)
        sorted_posts_id = [
            row[0]
            for row in all_rows[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
        ][::-1]
        return sorted_posts_id, all_post_count

    return [], 0


def get_timeline_fragment(
    method="tl",
    type_="",
    sorted_posts_id=[],
    all_post_count=0,
    sort_type="new",
    q="",
    user_name="",
    p=0,
):
    posts = {}
    users = {}
    external_posts = {}
    cnt = 0
    stop_load_reply = False
    while cnt < len(sorted_posts_id):
        if len(sorted_posts_id) > config.items_per_page * 10:
            logger.log(
                f"Warning: Large number of posts to load: {len(sorted_posts_id)}",
                type="warning",
                verbose=2,
            )
        if len(sorted_posts_id) > config.items_per_page * 20 and not stop_load_reply:
            logger.log(
                f"Error: Too many posts to load: {len(sorted_posts_id)}, there might be a loop in replies. Stopping further loading.",
                type="warning",
            )
            stop_load_reply = True
        post_id = sorted_posts_id[cnt]
        cnt += 1
        if post_id in posts:
            post = posts[post_id]
            # load reply info here, so that replies of duplicate posts are also handled
            if (
                post.isreply
                and post.reply_to
                and method != "fav"
                and not stop_load_reply
            ):
                reply_post_id, reply_user_name = post.reply_to.split("@")
                sorted_posts_id.insert(cnt, reply_post_id)
                external_posts[reply_post_id] = (post.type, reply_user_name)
            continue
        if post_id in ("redgifs",):
            continue

        post = backend.Post(post_id, None, None)
        if not post.load_from_db():
            if method == "tl" or method == "user":
                post.isplaceholder = True
                post.type, post.user_name = external_posts.get(post_id, ("", ""))
                post.concat_url()
                logger.log(f"guessed url for external post: {post.url}", verbose=3)
            elif method == "fav":
                logger.log(f"Post [{post_id}] not found.", verbose=3)
                post.user_name = "None"
                post.text_content = (
                    f"This post is missing from file system. [{post_id}]"
                )
                post.fav = True
            else:
                post.user_name = "None"
                post.text_content = f"HOW DID YOU EVEN GET HERE? [{post_id}]"

        # Load reply info
        if post.isreply and post.reply_to and not stop_load_reply:
            logger.log(f"post {post.post_id} is a reply to {post.reply_to}", verbose=3)
            reply_post_id, reply_user_name = post.reply_to.split("@")
            sorted_posts_id.insert(cnt, reply_post_id)
            external_posts[reply_post_id] = (post.type, reply_user_name)

        post.init_medias()
        post.init_embed()
        posts[post_id] = post

        # Load user info
        if post.user_name not in users:
            user = backend.User(post.user_name, post.type)
            user.load_from_db()
            if method == "fav" and post.user_name == "None":
                user.nick = "None"
            users[post.user_name] = user

    sorted_posts_id = sorted_posts_id[::-1]

    # Remove duplicates while preserving order
    seen = set()
    unique_sorted_posts_id = []
    for post_id in sorted_posts_id:
        if post_id not in seen:
            seen.add(post_id)
            unique_sorted_posts_id.append(post_id)
    sorted_posts_id = unique_sorted_posts_id

    # Determine page URL and rendering options
    if method == "tl":
        page_url = f"{config.url_base}/tl"
    elif method == "fav":
        page_url = f"{config.url_base}/fav"
    else:  # user
        if user_name in users:
            user_obj = users[user_name]
        else:
            user_obj = backend.User(user_name, type_)
            user_obj.load_from_db()
            users[user_name] = user_obj
        page_url = f"{config.url_base}/user/{type_}/{user_name}"

    # Render timeline
    timeline_content = render_template(
        "timeline.html",
        section=method,
        posts=posts,
        sorted_posts_id=sorted_posts_id,
        items_per_page=config.items_per_page,
        user_name=user_name if method == "user" else "",
        type=type_ if method == "user" else ("tl" if method == "tl" else ""),
        users=users,
        url_base=config.url_base,
        page_url=page_url,
        sort_type=sort_type,
        q=q,
        p=p + 1,
    )

    # Build content with optional headers
    if method == "tl":
        search_bar = render_template("searchbar.html", url_base=config.url_base)
        content = search_bar + timeline_content
    elif method == "fav":
        content = timeline_content
    else:  # user
        userheader = render_template(
            "userheader.html",
            type=type_,
            user=user_obj,
            url_base=config.url_base,
            posts_cnt=all_post_count,
        )
        content = userheader + timeline_content
    return content


def get_mediagrid_fragment(
    method="tl",
    type_="",
    sorted_posts_id=[],
    all_post_count=0,
    sort_type="new",
    q="",
    user_name="",
    p=0,
):
    posts = {}
    users = {}
    external_posts = {}

    cnt = 0
    for post_id in sorted_posts_id:
        post = backend.Post(post_id, None, None)
        post.load_from_db()
        post.init_medias()
        posts[post_id] = post

    # Determine page URL and rendering options
    if method == "tl":
        page_url = f"{config.url_base}/tl"
    elif method == "fav":
        page_url = f"{config.url_base}/fav"
    else:  # user
        if user_name in users:
            user_obj = users[user_name]
        else:
            user_obj = backend.User(user_name, type_)
            user_obj.load_from_db()
            users[user_name] = user_obj
        page_url = f"{config.url_base}/user/{type_}/{user_name}"

    # Render timeline
    timeline_content = render_template(
        "mediagrid.html",
        section=method,
        posts=posts,
        sorted_posts_id=sorted_posts_id[::-1],
        items_per_page=config.items_per_page,
        user_name=user_name if method == "user" else "",
        type=type_ if method == "user" else ("tl" if method == "tl" else ""),
        users=users,
        url_base=config.url_base,
        page_url=page_url,
        sort_type=sort_type,
        q=q,
        p=p + 1,
    )

    # Build content with optional headers
    if method == "tl":
        search_bar = render_template("searchbar.html", url_base=config.url_base)
        content = search_bar + timeline_content
    elif method == "fav":
        content = timeline_content
    else:  # user
        userheader = render_template(
            "userheader.html",
            type=type_,
            user=user_obj,
            url_base=config.url_base,
            posts_cnt=all_post_count,
        )
        content = userheader + timeline_content
    return content


def _timeline(method="tl", type_="", user_name=""):
    """
    Unified timeline function that handles overall timeline, favorites, and user timeline.
    :param method: "tl", "fav", or "user"
    :param type_: user type like "x", "bsky", "reddit", "fa" (only used for "user" method)
    :param user_name: user name (only used for "user" method)
    """
    global tl_current_sort, tl_current_page
    # if utils.busy_flag:
    #     return render_template("busy.html", url_base=config.url_base)
    user_name = user_name.lower()

    page = int(request.args.get("p", "1")) - 1
    page = max(0, page)
    tab = request.args.get("tab", "posts")
    query = request.args.get("q", "").strip()

    if "sort_type" in request.args:
        sort_type = request.args["sort_type"]
        if method == "tl":
            tl_current_sort = sort_type
    else:
        if method == "tl":
            sort_type = tl_current_sort
        else:
            sort_type = "new"

    if method == "tl":
        if "p" in request.args:
            tl_current_page[tl_current_sort] = page
        elif not query:
            page = tl_current_page[tl_current_sort]

    if method == "tl":
        sorted_posts_id, all_post_count = get_posts(
            method="tl", query=query, sort_type=sort_type, page=page
        )
        page_url = f"{config.url_base}/tl"
        if not query and sort_type not in ("new", "top", "random"):
            return "Invalid sort type."
        current_url = (
            posixpath.join("/", config.url_base, "tl")
            + "?tab="
            + tab
            + "&sort_type="
            + sort_type
        )
    elif method == "fav":
        try:
            sorted_posts_id, all_post_count = get_posts(method="fav", page=page)
        except ValueError:
            return "Not enough posts. Download more and come back later."
        page_url = f"{config.url_base}/fav"
        current_url = posixpath.join("/", config.url_base, "fav") + "?tab=" + tab
    elif method == "user":
        sorted_posts_id, all_post_count = get_posts(
            method="user",
            page=page,
            sort_type=sort_type,
            user_name=user_name,
            type_=type_,
        )
        page_url = f"{config.url_base}/user/{type_}/{user_name}"
        user_name = user_name.lower()
        user_obj = backend.User(user_name, type_)
        user_obj.load_from_db()
        current_url = (
            posixpath.join("/", config.url_base, "user", type_, user_name)
            + "?tab="
            + tab
            + "&sort_type="
            + sort_type
        )

    max_page = ceil(all_post_count / config.items_per_page)

    # Handle media tab for fav and user methods
    if tab == "media":
        content_frag = get_mediagrid_fragment(
            method=method,
            type_=type_,
            sorted_posts_id=sorted_posts_id,
            all_post_count=all_post_count,
            sort_type=sort_type,
            q=query,
            user_name=user_name,
            p=page,
        )
    else:
        content_frag = get_timeline_fragment(
            method=method,
            type_=type_,
            sorted_posts_id=sorted_posts_id,
            all_post_count=all_post_count,
            sort_type=sort_type,
            q=query,
            user_name=user_name,
            p=page,
        )

    # Build nav template kwargs
    nav_kwargs = {
        "content": content_frag,
        "current_page": page + 1,
        "current_url": current_url,
        "max_page": max_page,
        "section": method,
        "url_base": config.url_base,
        "shorts_decoration": "",
        "shorts_icon": "",
        "shorts_q": "",
    }

    if method == "tl":
        nav_kwargs["current_q"] = query
        # nav_kwargs["shorts_q"] = query
        # nav_kwargs["shorts_decoration"] = query[0] if query else ""
    elif method == "fav":
        nav_kwargs["adjust_padding_top"] = "0.5rem"
        nav_kwargs["shorts_q"] = "fav"
        nav_kwargs["shorts_icon"] = posixpath.join(
            "/", config.url_base, "img/bookmark_empty.svg"
        )
    else:  # user
        nav_kwargs["adjust_padding_top"] = "4rem"
        nav_kwargs["title_str"] = f"{user_obj.nick}"
        nav_kwargs["title_secondary_str"] = f"{all_post_count} posts"
        nav_kwargs["alt_home_icon"] = posixpath.join(
            "/", config.url_base, "avatar", type_, user_name
        )
        nav_kwargs["title"] = f"{user_obj.nick} (@{user_name}) - {type_}"
        nav_kwargs["user"] = user_obj
        nav_kwargs["shorts_q"] = f"{user_obj.uid}"
        nav_kwargs["shorts_icon"] = posixpath.join(
            "/", config.url_base, "avatar", type_, user_name
        )
        nav_kwargs["show_back_btn"] = True
    return render_template("nav.html", **nav_kwargs)


def build_app():
    app = Flask(__name__)

    @app.after_request
    def set_csp_header(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:;"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.route(posixpath.join("/", config.url_base, "mt.webmanifest"))
    def _webmanifest():
        with open("templates/mt.webmanifest", "r") as f:
            return render_template_string(f.read(), url_base=config.url_base)

    if config.url_base.strip("/") != "":

        @app.route("/")
        def _root():
            return redirect(posixpath.join("/", config.url_base, "tl"))

    @app.route(posixpath.join("/", config.url_base, "js", "<fn>"))
    def _js(fn):
        if ".." in fn or not os.path.exists(posixpath.join("js", fn)):
            logger.log(f"File not found: js/{fn}.")
            return "File not found.", 404
        return set_cache_header(send_from_directory("js", fn))

    @app.route(posixpath.join("/", config.url_base, "css", "<fn>"))
    def _css(fn):
        if ".." in fn or not os.path.exists(posixpath.join("css", fn)):
            logger.log(f"File not found: css/{fn}.")
            return "File not found.", 404
        return set_cache_header(send_from_directory("css", fn))

    @app.route(posixpath.join("/", config.url_base, "img", "<fn>"))
    def _img(fn):
        if ".." in fn or not os.path.exists(posixpath.join("img", fn)):
            logger.log(f"File not found: img/{fn}.")
            return "File not found.", 404
        return set_cache_header(send_from_directory("img", fn))

    @app.route(posixpath.join("/", config.url_base, "avatar", "<type>", "<name>"))
    def _avatar(type, name):
        # logger.log(type, name)
        name = name.lower()
        force_redownload = request.args.get("redownload", "0") == "1"
        if not type or type == "None":
            return send_file("img/default_avatar.png", mimetype="image/jpeg")
        fn = f"{config.fs_bases[type]}/{name}/avatar"
        fn_bck = f"{config.fs_bases[type]}/{name}/avatar_bck"
        if not os.path.exists(fn) or force_redownload:
            user = backend.User(name, type)
            user.load_from_db()
            avatar_url = user.avatar
            if (not user.avatar) or (not avatar_url.startswith("http")) or user.flagged:
                if os.path.exists(f"tmp/.cached/{name}.gif"):
                    return set_cache_header(
                        send_file(f"tmp/.cached/{name}.gif", mimetype="image/gif")
                    )
                if user.flagged:
                    logger.log(name, "is flagged, skip avatar downloading.")
                if os.path.exists(fn_bck):
                    logger.log("copying", fn_bck, "to", fn)
                    with open(fn_bck, "rb") as f:
                        with open(fn, "wb") as f2:
                            f2.write(f.read())
            else:
                logger.log(
                    "Downloading avatar:", avatar_url, "for", name, type="attention"
                )
                r = utils.get(avatar_url)
                if r.status_code == 200:
                    with open(fn, "wb") as f:
                        f.write(r.content)
                else:
                    logger.log(
                        f"Failed to download avatar for {name} from {avatar_url}, status code: {r.status_code}",
                        type="error",
                    )
                    # Use backup if exists
                    if os.path.exists(fn_bck):
                        logger.log("copying", fn_bck, "to", fn)
                        with open(fn_bck, "rb") as f:
                            with open(fn, "wb") as f2:
                                f2.write(f.read())
                    else:
                        # copy default avatar
                        logger.log(f"Copying default avatar to {fn}", type="attention")
                        if type == "reddit":
                            with open("img/reddit.png", "rb") as f:
                                with open(fn, "wb") as f2:
                                    f2.write(f.read())
                        else:
                            with open("img/default_avatar.png", "rb") as f:
                                with open(fn, "wb") as f2:
                                    f2.write(f.read())
        # Check file size
        if not os.path.exists(fn) or os.path.getsize(fn) < 100:
            logger.log(f"Avatar file {fn} is too small or does not exist.", verbose=1)
            if os.path.exists(fn_bck):
                return set_cache_header(send_file(fn_bck, mimetype="image/jpeg"))
            else:
                if type == "reddit":
                    return set_cache_header(
                        send_file("img/reddit.png", mimetype="image/jpeg")
                    )
                else:
                    return set_cache_header(
                        send_file("img/default_avatar.png", mimetype="image/jpeg")
                    )
        return set_cache_header(send_file(fn, mimetype="image/jpeg"))

    @app.route(posixpath.join("/", config.url_base, "banner", "<type>", "<name>"))
    def _banner(type, name):
        # logger.log(type, name)
        name = name.lower()
        force_redownload = request.args.get("redownload", "0") == "1"
        if not type or type == "None":
            return send_file("img/default_avatar.png", mimetype="image/jpeg")
        fn = f"{config.fs_bases[type]}/{name}/banner"
        fn_bck = f"{config.fs_bases[type]}/{name}/banner_bck"
        if not os.path.exists(fn) or force_redownload:
            user = backend.User(name, type)
            user.load_from_db()
            banner_url = user.banner
            if (not user.banner) or (not banner_url.startswith("http")) or user.flagged:
                if user.flagged:
                    logger.log(name, "is flagged, skip banner downloading.")
                if os.path.exists(fn_bck):
                    logger.log("copying", fn_bck, "to", fn)
                    with open(fn_bck, "rb") as f:
                        with open(fn, "wb") as f2:
                            f2.write(f.read())
            else:
                logger.log(
                    "Downloading banner:", banner_url, "for", name, type="attention"
                )
                r = utils.get(banner_url)
                if r.status_code == 200:
                    with open(fn, "wb") as f:
                        f.write(r.content)
                else:
                    logger.log(
                        f"Failed to download banner for {name} from {banner_url}. Status code: {r.status_code}",
                        type="error",
                    )
                    # Use backup if exists
                    if os.path.exists(fn_bck):
                        logger.log("copying", fn_bck, "to", fn)
                        with open(fn_bck, "rb") as f:
                            with open(fn, "wb") as f2:
                                f2.write(f.read())
                    else:
                        # copy default banner
                        logger.log(f"Copying default banner to {fn}", type="attention")
                        with open("img/empty.png", "rb") as f:
                            with open(fn, "wb") as f2:
                                f2.write(f.read())
        # Check file size
        if not os.path.exists(fn) or os.path.getsize(fn) < 100:
            logger.log(f"Banner file {fn} is too small or does not exist.", verbose=1)
            if os.path.exists(fn_bck):
                return set_cache_header(send_file(fn_bck, mimetype="image/jpeg"))
            else:
                return set_cache_header(
                    send_file("img/empty.png", mimetype="image/jpeg")
                )
        return set_cache_header(send_file(fn, mimetype="image/jpeg"))

    @app.route(posixpath.join("/", config.url_base + "/"))
    @app.route(posixpath.join("/", config.url_base))
    def _index():
        return render_template("frame.html", url_base=config.url_base)

    @app.route(posixpath.join("/", config.url_base, "userlist"))
    @check_auth()
    def _userlist():
        if "p" in request.args:
            page = int(request.args["p"]) - 1
            page = max(0, page)
        else:
            page = 0

        query = request.args.get("q", "").strip()
        tab = request.args.get("tab", "all")
        seach_bar = render_template("searchbar.html", url_base=config.url_base)
        if tab == "all":
            if query:
                fuzz_query = (
                    query.replace("https://", "")
                    .replace("x.com/", "")
                    .replace("bsky.app/profile/", "")
                    .replace("twitter.com/", "")
                    .replace("www.reddit.com/r/", "")
                    .replace("reddit.com/r/", "")
                    .replace("furaffinity.net/user/", "")
                    .lower()
                )
                all_users = [
                    u
                    for u in backend.all_users
                    if fuzz_query in u.nick.lower()
                    or fuzz_query in u.uid.lower()
                    or fuzz_query in u.user_name.lower()
                    or fuzz_query in u.description.lower()
                ]
                users = all_users[
                    page * config.items_per_page : (page + 1) * config.items_per_page
                ]
                max_page = ceil(len(all_users) / config.items_per_page)
            else:
                users = backend.all_users[
                    page * config.items_per_page : (page + 1) * config.items_per_page
                ]
                max_page = ceil(len(backend.all_users) / config.items_per_page)
            userlist = render_template(
                "userlist.html", users=users, url_base=config.url_base, tab=tab
            )
        else:
            all_groups = backend.get_user_groups()
            max_page = ceil(len(all_groups) / config.items_per_page)
            if query:
                all_groups = [g for g in all_groups if query.lower() in g.lower()]
            groups = all_groups[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
            users_in_groups = {}
            for group in groups:
                user_ids = backend.get_uids_in_group(group)
                logger.log(f"Group '{group}' has users: {user_ids}", verbose=2)
                users = []
                for user_id in user_ids:
                    user = backend.User(user_id.split("@")[0], user_id.split("@")[1])
                    user.load_from_db()
                    users.append(user)
                users_in_groups[group] = users
            userlist = render_template(
                "userlist.html",
                groups=groups,
                users_in_groups=users_in_groups,
                url_base=config.url_base,
                tab=tab,
            )

        return render_template(
            "nav.html",
            current_page=page + 1,
            current_q=query,
            current_url=posixpath.join("/", config.url_base, "userlist"),
            max_page=max_page,
            content=seach_bar + userlist,
            section="user",
            url_base=config.url_base,
            shorts_decoration="",
            shorts_icon="",
        )

    @app.route(posixpath.join("/", config.url_base, "tl"))
    @check_auth()
    def _timeline_all():
        return _timeline(method="tl")

    @app.route(posixpath.join("/", config.url_base, "fav"))
    @check_auth()
    def _timeline_fav():
        return _timeline(method="fav")

    @app.route(posixpath.join("/", config.url_base, "user", "<type>", "<name>"))
    @check_auth()
    def _timeline_user(type, name):
        return _timeline(method="user", type_=type, user_name=name)

    @app.route(posixpath.join("/", config.url_base, "add_fav"))
    @check_auth()
    def _add_fav():
        post_id = request.args["post_id"]
        if backend.db.query_rows(
            selected_table="fav", key="post_id", value=post_id, ignore_cache=True
        ):
            logger.log("remove favorite", post_id)
            backend.remove_favorite(post_id)
            return {
                "result": "removed",
            }
        else:
            logger.log("add favorite", post_id)
            backend.add_favorite(post_id)
            return {
                "result": "added",
            }

    @app.route(
        posixpath.join(
            "/", config.url_base, "fullscreen_card", "<type>", "<name>", "<filename>"
        )
    )
    @check_auth()
    def _fullscreen_card(type, name, filename):

        if type in ["x", "bsky", "reddit"]:
            media_id = filename.split(".")[0]
        elif type == "fa":
            media_id = filename
        else:
            return "Invalid type."
        media = backend.Media(media_id, None, name, type)
        media.load_from_db()
        user = backend.User(name, type)
        user.load_from_db()
        post = backend.Post(media.post_id, name, type)
        post.load_from_db()

        media_ids = backend.db.query_rows(
            selected_table="media",
            key="post_id",
            value=media.post_id,
            sort_key=utils.sort_keys["e0"],
            reverse=False,
        )
        media_ids = [m[0] for m in media_ids]
        logger.log("media_ids for post", media.post_id, ":", media_ids, verbose=2)
        media_index = media_ids.index(media.media_id)
        logger.log("media_index:", media_index, verbose=2)
        if media_index > 0:
            prev_url = posixpath.join(
                config.url_base or "/",
                "fullscreen_card",
                type,
                name,
                f"{media_ids[media_index-1]}.jpg",
            )
        else:
            prev_url = ""
        if media_index < len(media_ids) - 1:
            next_url = posixpath.join(
                config.url_base or "/",
                "fullscreen_card",
                type,
                name,
                f"{media_ids[media_index+1]}.jpg",
            )
        else:
            next_url = ""

        card = render_template(
            "fullscreen_card.html",
            media=media,
            user=user,
            post=post,
            url_base=config.url_base,
            prev_url=prev_url,
            next_url=next_url,
            alt_text=post.alts[media_index] if media_index < len(post.alts) else "",
            media_idx=media_index,
        )
        return card

    @app.route(
        posixpath.join("/", config.url_base, "post", "<type>", "<name>", "<post_id>")
    )
    @check_auth()
    def _post(type, name, post_id):
        users = {}

        post = backend.Post(post_id, name, type)
        post_found = post.load_from_db()

        post.init_medias()
        post.init_embed()

        user = backend.User(name, type)
        user.load_from_db()
        users[name] = user

        reply_tos = [post]

        cursor = 0
        stop_load_reply = False
        while cursor < len(reply_tos) and not stop_load_reply:
            if cursor > 999:
                logger.log(
                    f"Warning: Too many parent posts to load for post {post_id}, there might be a loop in replies. Stopping further loading.",
                    type="warning",
                )
                stop_load_reply = True
            post = reply_tos[cursor]
            cursor += 1
            if post.reply_to and not stop_load_reply:
                reply_post_id, reply_user_name = post.reply_to.split("@")
                reply_post = backend.Post(reply_post_id, reply_user_name, type)
                if not reply_post.load_from_db():
                    logger.log(
                        f"Reply post {reply_post_id} not found for post {post.post_id}."
                    )
                    continue
                reply_user_name = reply_post.user_name
                reply_post.init_medias()
                reply_post.init_embed()
                reply_tos.append(reply_post)
                if reply_post.user_name not in users:
                    reply_user = backend.User(reply_user_name, type)
                    reply_user.load_from_db()
                    users[reply_user_name] = reply_user

        comments = [post]
        cursor = 0
        stop_load_reply = False
        while cursor < len(comments):
            if cursor > 999 and not stop_load_reply:
                logger.log(
                    f"Warning: Too many parent posts to load for post {post_id}, there might be a loop in replies. Stopping further loading.",
                    type="warning",
                )
                stop_load_reply = True
            post = comments[cursor]
            cursor += 1
            if not stop_load_reply:
                replies = backend.db.query_rows(
                    selected_table="posts",
                    key="reply_to",
                    value=f"{post.post_id}@{user.udid}",
                    reverse=False,
                )
            else:
                replies = []
            for reply in replies:
                reply_post_id = reply[0]
                reply_user_name = reply[2].split("@")[0]
                reply_post = backend.Post(reply_post_id, reply_user_name, type)
                if not reply_post.load_from_db():
                    logger.log(
                        f"Reply post {reply_post_id} not found for post {post.post_id}."
                    )
                    continue
                reply_user_name = reply_post.user_name
                reply_post.init_medias()
                reply_post.init_embed()
                comments.append(reply_post)
                if reply_user_name not in users:
                    reply_user = backend.User(reply_user_name, type)
                    reply_user.load_from_db()
                    users[reply_user_name] = reply_user

        seen_posts = set()
        all_related_posts = []
        for comment in comments:
            if comment.post_id not in seen_posts:
                all_related_posts.append(comment)
                seen_posts.add(comment.post_id)
        for reply_to in reply_tos:
            if reply_to.post_id not in seen_posts:
                all_related_posts.append(reply_to)
                seen_posts.add(reply_to.post_id)

        sorted_posts = sorted(all_related_posts, key=lambda x: x.time)

        logger.log(
            f"Loaded {len(comments)} comments for post {post_id}, and {len(reply_tos)-1} parent posts.",
            verbose=2,
        )
        logger.log(
            "Users involved in the comment thread:", list(users.keys()), verbose=2
        )
        content_frag = render_template(
            "comments.html",
            highlight_post_id=post_id,
            reply_tos=reply_tos,
            comments=comments,
            sorted_posts=sorted_posts,
            users=users,
            url_base=config.url_base,
            post_found=post_found,
            detailed_view=True,
        )
        nav_kwargs = {
            "content": content_frag,
            "current_page": 1,
            "current_url": posixpath.join(
                "/", config.url_base, "comments", type, name, post_id
            ),
            "max_page": 1,
            "section": "comments",
            "url_base": config.url_base,
            "shorts_decoration": "",
            "shorts_icon": "",
            "show_back_btn": True,
            "adjust_padding_top": "5rem",
            "title_str": "Post by " + users[name].nick if name in users else "Post",
            "title_secondary_str": f"{len(all_related_posts)} posts in this thread",
        }
        return render_template("nav.html", **nav_kwargs)

    @app.route(
        posixpath.join("/", config.url_base, "ruffle", "<type>", "<name>", "<filename>")
    )
    @check_auth()
    def _ruffle(type, name, filename):
        return render_template(
            "ruffle.html",
            type=type,
            user_name=name,
            file_name=filename,
            url_base=config.url_base,
        )

    @app.route(posixpath.join("/", config.url_base, "download"))
    @check_auth()
    def _download():
        download = render_template(
            "download.html",
            url_base=config.url_base,
        )
        return render_template(
            "nav.html",
            content=download,
            current_page=0,
            current_url="",
            max_page=0,
            section="download",
            url_base=config.url_base,
            shorts_decoration="",
            shorts_icon="",
        )

    @app.route(posixpath.join("/", config.url_base, "file", "<type>", "<name>", "<fn>"))
    @check_auth()
    def _file(type, name, fn):
        fn = unquote(fn)
        file_path = f"{config.fs_bases[type]}/{name}/{fn}"
        if not os.path.exists(file_path):
            logger.log(f"File not found: {file_path}.")
            return "File not found.", 404
        if ".." in file_path or '"' in file_path:
            logger.log(f"Security warning: suspicious path: {file_path}.", type="error")
            return "File not found.", 404
        return set_cache_header(send_file(file_path))

    @app.route(
        posixpath.join("/", config.url_base, "thumb", "<type>", "<name>", "<fn>")
    )
    @check_auth()
    def _thumb(type, name, fn):
        fn = unquote(fn)
        if fn.startswith("thumb_"):
            fn = fn[6:]
        size = config.thumbnail_size
        if "size" in request.args:
            size = int(request.args["size"])
            size = min(max(size, 32), 2500)
        path = f"{config.fs_bases[type]}/{name}/{fn}"
        if not os.path.exists(path):
            logger.log(f"File not found for thumbnail: {path}.")
            return (
                set_cache_header(send_file("img/error.jpg", mimetype="image/jpeg")),
                404,
            )
        if ".." in path or '"' in path:
            logger.log(
                f"Security warning: suspicious path for thumbnail: {path}.",
                type="error",
            )
            return (
                set_cache_header(send_file("img/error.jpg", mimetype="image/jpeg")),
                404,
            )
        thumbnail_path = utils.create_thumbnail(path, size)
        if not thumbnail_path or not os.path.exists(thumbnail_path):
            logger.log(f"Thumbnail not found for {path}.")
            return (
                set_cache_header(send_file("img/error.jpg", mimetype="image/jpeg")),
                404,
            )
        return set_cache_header(send_file(thumbnail_path, mimetype="image/jpeg"))

    @app.route(posixpath.join("/", config.url_base, "view", "<type>", "<name>", "<fn>"))
    @check_auth()
    def _view(type, name, fn):
        return render_template(
            "viewer.html",
            type=type,
            user_name=name,
            file_name=fn,
            isvideo=fn.endswith(".mp4") or fn.endswith(".webm"),
            url_base=config.url_base,
        )

    @app.route(
        posixpath.join("/", config.url_base, "api", "download"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_download_job():
        data = request.get_json()
        logger.log("Received data:", data, verbose=3)
        rescan = data.get("rescan", False)
        if not rescan:
            job_type = utils.TYPE_DOWNLOAD
        else:
            job_type = utils.TYPE_RESCAN
        if "url" in data and data["url"]:
            url = data["url"]
            if "?" in url:
                url = url.split("?")[0]
            full = data.get("full", False)
            media_only = data.get("media_only", False)
            if not (
                "bsky" in url
                or "x.com" in url
                or "twitter" in url
                or "reddit" in url
                or "furaffinity" in url
            ):
                msg = f"Invalid URL: {url}\n"
                logger.log(msg)
                return jsonify(
                    {
                        "status": "ok",
                        "message": msg,
                        "current": utils.current_url,
                        "queue": utils.download_jobs,
                    }
                )
            if "did:" in url:
                msg = f"Go get the actual bsky handle like 'xxx.bsky.social', {url} won't do.\n"
                logger.log(msg)
                return jsonify(
                    {
                        "status": "ok",
                        "message": msg,
                        "current": utils.current_url,
                        "queue": utils.download_jobs,
                    }
                )
            if re.search(r"\.bsky\.social", url):
                url = "https://bsky.app/profile/" + utils.filter_ascii(
                    url
                ).strip().replace("https://", "").replace("http://", "").replace(
                    "bsky.app/profile/", ""
                ).strip(
                    "/"
                )
            if not url.startswith("http"):
                url = f"https://{url}"
            url = url.replace("http://", "https://").strip("/")
            if url.endswith("/media"):
                url = url[:-6]
            if "twitter.com" in url:
                url = url.replace("twitter.com", "x.com")
            if "/photo/" in url:
                url = re.sub(r"photo/\d+", "", url)
            if "/video/" in url:
                url = re.sub(r"video/\d+", "", url)
            if rescan:
                utils.download_jobs.insert(0, (url, False, False, utils.TYPE_RESCAN))
                msg = "Rescan submitted."
            elif not (url, full, media_only, job_type) in utils.download_jobs:
                utils.download_jobs.insert(0, (url, full, media_only, job_type))
                msg = f"Job added.\n"
            else:
                msg = f"{url} already in download queue.\n"
            logger.log(msg)
        else:
            msg = ""
        return jsonify(
            {
                "status": "ok",
                "message": msg,
                "current": utils.current_url,
                "queue": utils.download_jobs,
            }
        )

    @app.route(posixpath.join("/", config.url_base, "api", "interrupt"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_interrupt():
        logger.log("Interrupt command received via API.")
        run_command.interrupt()
        return {"status": "ok", "message": "Interrupt signal sent to the downloader."}

    @app.route(posixpath.join("/", config.url_base, "shorts"), methods=["GET"])
    @check_auth()
    def _shorts():
        # if utils.busy_flag:
        #     return render_template("busy.html", url_base=config.url_base)
        type = request.args.get("type", "")
        user_name = request.args.get("user", "")
        uid = f"{user_name}@{type}"
        query = request.args.get("q", "")
        if query:
            if query == "fav":
                media_ids, total_cnt = backend.db.query_fav_videos()
            elif re.match(r"[\w.\-]+@\w+", query):
                media_ids, total_cnt = backend.db.query_video_by_uid(query, 0, 1)
            else:
                media_ids, total_cnt = backend.db.query_post_by_text(
                    query, 0, 1, include="video"
                )
            return render_template(
                "shorts.html",
                url_base=config.url_base,
                type=type,
                user=user_name,
                query=query,
                current_cnt=0,
                total_cnt=total_cnt,
            )
        else:
            user_name = ""
            idx = randint(0, 999999)
            return render_template(
                "shorts.html",
                url_base=config.url_base,
                type=type,
                current_cnt=idx,
                user=user_name,
                query=query,
                total_cnt=0,
            )

    @app.route(
        posixpath.join("/", config.url_base, "api", "get-a-vid"), methods=["GET"]
    )
    @check_auth()
    def _api_get_a_vid():
        user_name = request.args.get("user", "")
        type = request.args.get("type", "")
        uid = f"{user_name}@{type}"
        idx = int(request.args.get("idx", 0))
        query = request.args.get("q", "").strip()
        if query:
            if query == "fav":
                media_ids, _ = backend.db.query_fav_videos()
                media_ids = media_ids[::-1]
                idx = idx % len(media_ids) if len(media_ids) > 0 else 0
                if len(media_ids) == 0:
                    return {
                        "status": "error",
                        "message": f"No video found for query '{query}'.",
                    }
                media_id = media_ids[idx]
            elif re.match(r"[\w.\-]+@\w+", query):
                media_ids, _ = backend.db.query_video_by_uid(query, idx, 1)
                if len(media_ids) == 0:
                    return {
                        "status": "error",
                        "message": f"No video found for query '{query}'.",
                    }
                media_id = media_ids[0]
            else:
                media_ids, _ = backend.db.query_post_by_text(
                    query, idx, 1, include="video"
                )
                if len(media_ids) == 0:
                    return {
                        "status": "error",
                        "message": f"No video found for query '{query}'.",
                    }
                media_id = media_ids[0]
        else:
            if backend.db.get_video_count() == 0:
                return {
                    "status": "error",
                    "message": f"No video found.",
                }
            rowid = idx * 1234567 % backend.db.get_video_count()
            # rowid = idx % backend.db.get_video_count()
            media_id = backend.db.get_a_video(rowid + 1)
            if media_id and len(media_id) > 0:
                media_id = media_id[0][0]
            else:
                return {
                    "status": "error",
                    "message": f"No video found.",
                }
        media = backend.Media(media_id, None, None, None)
        media.load_from_db()
        post = backend.Post(media.post_id, None, None)
        post.load_from_db()
        user = backend.User(post.user_name, post.type)
        user.load_from_db()
        data = {
            "url": posixpath.join(
                "/", config.url_base, "file", post.type, post.user_name, media.file_name
            ),
            "preview": posixpath.join(
                "/",
                config.url_base,
                "thumb",
                post.type,
                post.user_name,
                media.file_name,
            ),
            "author": user.nick,
            "author_id": post.real_user if post.real_user else post.user_name,
            "avatar": f"{config.url_base}/avatar/{post.type}/{post.user_name}",
            "likes": post.likes,
            "comments": post.comments,
            "reposts": post.reposts,
            "description": post.text_content,
            "post_id": post.post_id,
            "media_id": media.media_id,
            "fav": bool(
                backend.db.query_rows(
                    selected_table="fav", key="post_id", value=post.post_id
                )
            ),
            "post_url": post.url,
            "user_url": f"{config.url_base}/user/{post.type}/{post.user_name}",
            "date": post.get_time_str(),
            "type": post.type,
        }
        return jsonify(data)

    @app.route(posixpath.join("/", config.url_base, "login"), methods=["GET", "POST"])
    def _login():
        if request.method == "GET":
            return render_template("login.html", url_base=config.url_base), 401
        elif request.method == "POST":
            logger.log("Login attempt.")
            pin = json.loads(request.data).get("pin", "")
            if pin == config.admin_pin_hased:
                session_key = utils.uuid()
                utils.auth_pool[session_key] = (time.time(), utils.ROLE_ADMIN)
                response = jsonify({"status": "ok", "message": "Login successful."})
                response.set_cookie("session_key", session_key, max_age=3600 * 24)
                return response
            elif pin == config.user_pin_hased:
                session_key = utils.uuid()
                utils.auth_pool[session_key] = (time.time(), utils.ROLE_AUTHORIZED)
                response = jsonify({"status": "ok", "message": "Login successful."})
                response.set_cookie("session_key", session_key, max_age=3600 * 24)
                return response
            else:
                return jsonify({"status": "error", "message": "Invalid PIN."})

    @app.route(posixpath.join("/", config.url_base, "logout"), methods=["GET", "POST"])
    def _logout():
        session_key = request.cookies.get("session_key", "")
        if session_key in utils.auth_pool:
            del utils.auth_pool[session_key]
        response = redirect(posixpath.join("/", config.url_base, "login"))
        response.set_cookie("session_key", "", expires=0)
        return response

    @app.route(posixpath.join("/", config.url_base, "settings"))
    @check_auth()
    def _settings():
        setting_frag = render_template(
            "settings.html", config=config, url_base=config.url_base
        )
        return render_template(
            "nav.html",
            current_page=0,
            current_q="",
            current_url=posixpath.join("/", config.url_base, "settings"),
            max_page=0,
            content=setting_frag,
            section="settings",
            url_base=config.url_base,
            shorts_decoration="",
            shorts_icon="",
        )

    @app.route(posixpath.join("/", config.url_base, "api", "favs"))
    @check_auth()
    def _api_favs():
        favs = backend.get_fav()
        fav_files = []
        for fav in favs:
            post_id = fav[0]
            fav_time = fav[1]  # Wed Jun  4 19:01:32 2025
            # to timestamp
            fav_time = time.mktime(time.strptime(fav_time, "%a %b %d %H:%M:%S %Y"))
            post = backend.Post(post_id, None, None)
            if not post.load_from_db():
                logger.log(f"Post [{post_id}] not found.")
                continue
            for row in backend.db.query_rows(
                selected_table="media", key="post_id", value=post_id
            ):
                media_id = row[0]
                media = backend.Media(media_id, post_id, None, post.type)
                media.load_from_db()
                fav_files.append(
                    (
                        os.path.join(
                            config.fs_bases[post.type], post.user_name, media.file_name
                        ),
                        fav_time,
                    )
                )
        return jsonify(fav_files)

    @app.route(posixpath.join("/", config.url_base, "api", "stats"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_stats():
        data = utils.get_stats()
        return jsonify(data)

    @app.route(posixpath.join("/", config.url_base, "api", "stop"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_stop():
        logger.log("Stop command received via API.")
        delayed_stop()
        return {"status": "ok", "message": "Server is stopping..."}

    @app.route(posixpath.join("/", config.url_base, "api", "flag_user"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_flag_user():
        uid = request.args.get("uid", "")
        user_name, type_ = uid.split("@")
        backend.flag_user(user_name, type_)
        logger.log(f"User {uid} has been flagged by admin.")
        return {"status": "ok", "message": f"User {uid} has been flagged."}

    @app.route(posixpath.join("/", config.url_base, "api", "unflag_user"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_unflag_user():
        uid = request.args.get("uid", "")
        user_name, type_ = uid.split("@")
        backend.unflag_user(user_name, type_)
        logger.log(f"User {uid} has been unflagged by admin.")
        return {"status": "ok", "message": f"User {uid} has been unflagged."}

    @app.route(
        posixpath.join("/", config.url_base, "api", "group_users"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_group_users():
        data = request.get_json()
        group_name = data.get("group_name", "")
        uids = data.get("uids", [])
        for uid in uids:
            backend.add_user_to_group(uid, group_name)
        logger.log(f"Users {uids} have been added to group {group_name} by admin.")
        return {
            "status": "ok",
            "message": f"Users {uids} have been added to group {group_name}.",
        }

    @app.route(
        posixpath.join("/", config.url_base, "api", "ungroup_users"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_ungroup_users():
        data = request.get_json()
        group_name = data.get("group_name", "")
        uids = backend.get_uids_in_group(group_name)
        for uid in uids:
            backend.remove_user_from_group(uid, group_name)
        logger.log(f"Users {uids} have been removed from group {group_name} by admin.")
        return {
            "status": "ok",
            "message": f"Users {uids} have been removed from group {group_name}.",
        }

    @app.route(
        posixpath.join("/", config.url_base, "api", "rename_group"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_rename_group():
        data = request.get_json()
        old_group_name = data.get("old_group_name", "")
        new_group_name = data.get("new_group_name", "")
        backend.rename_group(old_group_name, new_group_name)
        logger.log(
            f"Group {old_group_name} has been renamed to {new_group_name} by admin."
        )
        return {
            "status": "ok",
            "message": f"Group {old_group_name} has been renamed to {new_group_name}.",
        }

    @app.route(posixpath.join("/", config.url_base, "api", "logs"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_logs():
        lines = int(request.args.get("lines", 20))
        log_lines = logger.get_recent_logs(lines)
        return {"status": "ok", "message": "", "logs": log_lines}

    @app.route(posixpath.join("/", config.url_base, "logs"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _logs():
        log_lines = logger.get_recent_logs(200)
        return render_template(
            "logs.html", log_lines=log_lines, url_base=config.url_base
        )

    @app.route(posixpath.join("/", config.url_base, "cache_proxy", "<path:subpath>"))
    @check_auth()
    def cache_proxy(subpath):
        if not utils.check_link_allowed(subpath):
            return "Not allowed.", 403
        logger.log(f"Proxying request for: {subpath}", type="attention")
        subpath = subpath.replace("http:", "").replace("https:", "")
        subpath = subpath.lstrip("/")
        if "itch." in subpath:
            # quote url and recover '=', for some reason itch.io ask for plain '=' in url but not other characters?
            # and why would you include "%2F"('/' slash) in your filename? intentionaly messing with automation tools?
            # no need to further investigation, itch.io's url is a mess, no worth to fix it
            subpath = quote(subpath)
            subpath = subpath.replace("%3D", "=")
        if "furaffinity.net" in subpath:
            filename = subpath.split("/")[-1]
        else:
            filename = utils.filter_ascii("_".join(subpath.split("/")))
        subpath = "https://" + subpath
        cache_path = os.path.join("tmp/.cached", filename)
        if os.path.exists(cache_path):
            logger.log(f"Serving from cache: {cache_path}", verbose=1)
            return set_cache_header(send_file(cache_path))
        else:
            os.makedirs("tmp/.cached", exist_ok=True)
            logger.log(f"Fetching from remote: {subpath}", type="attention")
            try:
                r = utils.get(subpath)
                bin_ = r.content
                if len(bin_) < 1024:
                    raise Exception("Too small")
                with open(cache_path, "wb") as f:
                    f.write(bin_)
                logger.log(f"Cached to: {cache_path}")
            except Exception as e:
                logger.log(e, type="error")
                with open("img/empty.png", "rb") as f1:
                    with open(cache_path, "wb") as f2:
                        f2.write(f1.read())
                logger.log(
                    f"Failed to get {subpath}, use dummy image for {cache_path}",
                    type="error",
                )
            return set_cache_header(send_file(cache_path))

    return app


def init(skip_scan):
    if not skip_scan:
        backend.scan_for_users("x")
        backend.scan_for_users("bsky")
        backend.scan_for_users("reddit")
        backend.scan_for_users("fa")

        backend.scan_for_posts("x")
        backend.scan_for_posts("bsky")
        backend.scan_for_posts("reddit")
        backend.scan_for_posts("fa")
    backend.db.commit()
    backend.all_users = backend.get_users()
    logger.log("Scan finished.")

    if args.update_daemon:
        logger.log("Starting update daemon...")
        Thread(target=utils.update_daemon, daemon=True).start()


def shutdown_cleanup():
    print("Shutting down, performing cleanup...")
    utils.global_running_flag = False
    backend.db.commit()
    logger.log("Cleanup done. Goodbye!")


def signal_handler(signal, frame):
    shutdown_cleanup()
    sys.exit(0)


def wsgi_app(skip_scan=False, config_file="config.json"):
    print("Starting app with wsgi_app()")
    config.read_config(config_file)
    init(skip_scan)
    app = build_app()
    logger.log(f"app is ready at: http://{config.host}:{config.port}{config.url_base}")
    return app


def delayed_stop():
    def stop():
        backend.db.commit()
        time.sleep(1)  # Wait a moment to ensure the response is sent before stopping
        os.kill(os.getpid(), signal.SIGTERM)

    Thread(target=stop, daemon=True).start()


db = backend.Database("data.db", "fav.db")
db.prepare_db()
backend.set_db(db)
utils.global_running_flag = True
worker = utils.DownloadWorker(db)
worker.setDaemon(True)
worker.start()
logger.log("Download worker started.")

if args.monitor_timeline:
    logger.log("Initializing bsky monitor...")
    bsky_monitor = live_timeline_monitor.BlueskyTimelineMonitor()
    if bsky_monitor.start() == 0:
        logger.log("Bluesky monitor started.")
    else:
        logger.log("Failed to start Bluesky monitor.", type="error")

logger.log("Ready.")

if __name__ == "__main__":
    init(args.skip_scan)
    app = build_app()
    logger.log(f"app is ready at: http://{config.host}:{config.port}{config.url_base}")
    app.run(host=config.host, port=config.port, debug=args.debug)
    shutdown_cleanup()
    sys.exit(0)
else:
    signal.signal(signal.SIGTERM, signal_handler)  # Handle SIGTERM
