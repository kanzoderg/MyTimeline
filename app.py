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
from html import escape, unescape
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

parser.add_argument("--skip-scan", action="store_true", help="Skip startup posts scan.")
parser.add_argument(
    "--skip-scan-users", action="store_true", help="Skip startup user scan."
)
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

import backend, utils, logger, run_command, live_timeline_monitor, database

logger.VERBOSE_LEVEL = args.verbose
backend.debug_mode = args.debug
if args.debug:
    logger.VERBOSE_LEVEL = max(logger.VERBOSE_LEVEL, 1)
    logger.log("Debug mode enabled.", verbose=1)
args.skip_scan = bool(args.skip_scan)
args.skip_scan_users = bool(args.skip_scan_users)
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


# tl_current_sort = "new"
# tl_current_page = {
#     "new": 0,
#     "top": 0,
#     "random": 0,
# }


def get_posts(
    method="tl",
    query="",
    sort_type="new",
    page=0,
    user_name="",
    type_="",
    media_only=False,
):
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
    media_only = False  # disable media only filter for now, performance concern

    if method == "tl":
        if query:
            sorted_posts_ids, all_post_count = database.db.query_post_by_text(
                query,
                page * config.items_per_page,
                config.items_per_page,
                sort_type=sort_type,
            )
            sorted_posts_ids = sorted_posts_ids[::-1]
        else:
            all_post_count = database.db.get_post_count()
            if sort_type == "new":
                sorted_posts_ids, _ = database.db.get_new(
                    page * config.items_per_page, config.items_per_page
                )
            elif sort_type == "top":
                sorted_posts_ids, _ = database.db.get_top(
                    page * config.items_per_page, config.items_per_page
                )
            elif sort_type == "random":
                sorted_posts_ids = database.db.get_random(config.items_per_page)
            else:
                return [], 0
            sorted_posts_ids = sorted_posts_ids[::-1]
        return sorted_posts_ids, all_post_count

    elif method == "fav":
        sorted_posts_ids = backend.get_fav()
        all_post_count = len(sorted_posts_ids)
        sorted_posts_ids = sorted_posts_ids[
            page * config.items_per_page : (page + 1) * config.items_per_page
        ][::-1]
        return sorted_posts_ids, all_post_count

    elif method == "user":
        if not user_name or not type_:
            return [], 0
        uid = f"{user_name}@{type_}"
        if sort_type == "new":
            sorted_posts_ids, all_post_count = database.db.get_new(
                page * config.items_per_page,
                config.items_per_page,
                uid=uid,
                media_only=media_only,
            )
        elif sort_type == "top":
            sorted_posts_ids, all_post_count = database.db.get_top(
                page * config.items_per_page,
                config.items_per_page,
                uid=uid,
                media_only=media_only,
            )
        elif sort_type == "random":
            all_post_count = database.db.get_post_count()
            sorted_posts_ids = database.db.get_random(config.items_per_page)
        else:
            return [], 0
        sorted_posts_ids = sorted_posts_ids[::-1]
        return sorted_posts_ids, all_post_count

    return [], 0


@utils.time_it
def get_post_groups_by_ids(
    post_ids, load_reply=True, load_comments=False, sort_groups=False
):

    posts = {}
    users = {}
    post_groups = {}

    cursor = 0
    post_ids_queue = [(pid, "", "", 0, 0) for pid in post_ids]
    seen = set()

    while cursor < len(post_ids_queue):
        if len(post_ids_queue) > 1000:
            logger.log(
                f"Warning: post_ids_queue length is {len(post_ids_queue)}, which may indicate a problem with loading posts.",
                type="warning",
            )
        if len(post_ids_queue) > 10000:
            logger.log(
                f"Error: post_ids_queue length is {len(post_ids_queue)}, which indicates a serious problem with loading posts. Breaking the loop.",
                type="error",
            )
            break

        post_id, group_id, username, _time, _piority = post_ids_queue[cursor]
        cursor += 1

        if post_id in seen:
            continue
        seen.add(post_id)

        post = backend.Post(post_id, username, post_id.split("@")[1])
        post.piority = _piority
        if post.load_from_db():
            post.init_medias()
            post.init_embeds()

        posts[post_id] = post
        if not post.user_name in users:
            user = backend.User(post.user_name, post.type)
            user.load_from_db()
            users[post.user_name] = user
        post.user = users[post.user_name]

        if post.reply_root_id:
            group_id = post.reply_root_id
        elif post.reply_to_id:
            group_id = post.reply_to_id
        elif not group_id:
            group_id = post_id

        if not post.reply_to_id and group_id != post_id:
            post.reply_to_id = group_id

        if not post_id in post_groups.get(group_id, {}):
            post_groups.setdefault(group_id, {})[post_id] = post

        if post.isplaceholder:
            logger.log(
                f"Post {post_id} is a placeholder, skipping loading replies and comments.",
                type="warning",
                verbose=2,
            )
            logger.log(post_id, group_id, username, _time, type="warning")
            continue

        if load_reply:
            # Add reply_to and reply_root posts to group
            if post.reply_to_id and not post.reply_to_id in post_groups.get(
                group_id, {}
            ):
                try:
                    reply_to_username = post.reply_to.split("@")[1]
                except:
                    logger.log(
                        f"Error parsing reply_to username for post {post_id} with reply_to {post.reply_to}",
                        type="error",
                    )
                    reply_to_username = ""
                post_ids_queue.insert(
                    cursor,
                    (
                        post.reply_to_id,
                        group_id,
                        reply_to_username,
                        post.time,
                        post.time - 1,
                    ),
                )

            if post.reply_root_id and not post.reply_root_id in post_groups.get(
                group_id, {}
            ):
                try:
                    reply_root_username = post.reply_root.split("@")[1]
                except:
                    logger.log(
                        f"Error parsing reply_root username for post {post_id} with reply_root {post.reply_root}",
                        type="error",
                    )
                    reply_root_username = ""
                post_ids_queue.insert(
                    cursor,
                    (
                        post.reply_root_id,
                        group_id,
                        reply_root_username,
                        post.time,
                        1,
                    ),
                )
            elif post.reply_root_id in post_groups.get(group_id, {}):
                post_groups[group_id][post.reply_root_id].piority = 1

        if load_comments:
            # Add comments to group
            comments_post_ids = database.db.get_comments(
                f"{group_id.split('@')[0]}@{user.udid}", type=post.type
            )
            for comment_post_id, username in comments_post_ids:
                if not comment_post_id in post_groups.get(group_id, {}):
                    post_ids_queue.append(
                        (
                            comment_post_id,
                            group_id,
                            username,
                            post.time,
                            0,
                        )
                    )

    post_groups_list = []
    for group_id, group_posts in post_groups.items():
        group = []
        for post_id, post in group_posts.items():
            group.append(post)
        group.sort(key=lambda x: (x.piority or x.time, x.post_id), reverse=False)
        # group = group[::-1]
        post_groups_list.append(group)
    if sort_groups:
        post_groups_list.sort(key=lambda x: x[-1].time, reverse=True)
    else:
        post_groups_list = post_groups_list[::-1]

    return post_groups_list


def get_timeline_content(
    method="tl",
    type_="",
    sorted_posts_ids=[],
    all_post_count=0,
    sort_type="new",
    q="",
    user_name="",
    p=0,
    tab="posts",
    frag_only=False,
):
    post_groups = get_post_groups_by_ids(
        sorted_posts_ids,
        load_reply=tab == "posts",
        load_comments=False,
        sort_groups=False,
    )
    if tab == "media" and frag_only:
        # count media
        media_cnt = 0
        for group in post_groups:
            for post in group:
                media_cnt += len(post.medias)
        if media_cnt == 0:
            return ""

    # Determine page URL and rendering options
    if method == "tl":
        page_url = f"{config.url_base}/tl"
    elif method == "fav":
        page_url = f"{config.url_base}/fav"
    else:  # user
        user_obj = backend.User(user_name, type_)
        user_obj.load_from_db()
        page_url = f"{config.url_base}/user/{type_}/{user_name}"

    if frag_only:
        if tab == "media":
            template_to_render = "mediagrid_frag.html"
        else:
            template_to_render = "timeline_frag.html"
    else:
        template_to_render = "timeline.html"

    # Render timeline
    timeline_content = render_template(
        template_to_render,
        tab=tab,
        section=method,
        anchor_user=user_obj if method == "user" else None,
        post_groups=post_groups,
        sorted_posts_ids=sorted_posts_ids,
        items_per_page=config.items_per_page,
        user_name=user_name if method == "user" else "",
        type=type_ if method == "user" else ("tl" if method == "tl" else ""),
        url_base=config.url_base,
        page_url=page_url,
        sort_type=sort_type,
        q=q,
        p=p + 1,
        quote=quote,
        unquote=unquote,
    )

    if frag_only:
        return timeline_content

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
            tab=tab,
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
    # global tl_current_sort, tl_current_page
    # if utils.busy_flag:
    #     return render_template("busy.html", url_base=config.url_base)
    user_name = user_name.lower()
    if "@" in user_name:
        user_name = user_name.split("@")[0]

    page = int(request.args.get("p", "1")) - 1
    page = max(0, page)
    tab = request.args.get("tab", "posts")
    query = request.args.get("q", "").strip()
    sort_type = request.args.get("sort_type", "new").strip()
    frag_only = request.args.get("frag_only", "0") == "1"

    if method == "tl":
        sorted_posts_ids, all_post_count = get_posts(
            method="tl",
            query=query,
            sort_type=sort_type,
            page=page,
            media_only=(tab == "media"),
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
            sorted_posts_ids, all_post_count = get_posts(
                method="fav", page=page, media_only=(tab == "media")
            )
        except ValueError:
            return "Not enough posts. Download more and come back later."
        page_url = f"{config.url_base}/fav"
        current_url = posixpath.join("/", config.url_base, "fav") + "?tab=" + tab
    elif method == "user":
        sorted_posts_ids, all_post_count = get_posts(
            method="user",
            page=page,
            sort_type=sort_type,
            user_name=user_name,
            type_=type_,
            media_only=(tab == "media"),
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

    content_frag = get_timeline_content(
        method=method,
        type_=type_,
        sorted_posts_ids=sorted_posts_ids,
        all_post_count=all_post_count,
        sort_type=sort_type,
        q=query,
        user_name=user_name,
        p=page,
        tab=tab,
        frag_only=frag_only,
    )

    if frag_only:
        if tab == "media" and content_frag.strip() == "":
            return {"status": "continue", "content": ""}
        return {"status": "success", "content": content_frag}

    # Build nav template kwargs
    nav_kwargs = {
        "content": content_frag,
        "current_page": page + 1,
        "current_tab": tab,
        "sort_type": sort_type,
        "current_url": current_url,
        "max_page": max_page,
        "section": method,
        "url_base": config.url_base,
        "shorts_decoration": "",
        "shorts_icon": "",
        "shorts_q": "",
        "tab": tab,
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
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; img-src 'self' data:;"
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
        if (
            ".." in fn
            or "/" in fn
            or "\\" in fn
            or not os.path.exists(posixpath.join("js", fn))
        ):
            logger.log(f"File not found: js/{fn}.")
            return "File not found.", 404
        return set_cache_header(send_from_directory("js", fn))

    @app.route(posixpath.join("/", config.url_base, "css", "<fn>"))
    def _css(fn):
        if (
            ".." in fn
            or "/" in fn
            or "\\" in fn
            or not os.path.exists(posixpath.join("css", fn))
        ):
            logger.log(f"File not found: css/{fn}.")
            return "File not found.", 404
        return set_cache_header(send_from_directory("css", fn))

    @app.route(posixpath.join("/", config.url_base, "img", "<fn>"))
    def _img(fn):
        if (
            ".." in fn
            or "/" in fn
            or "\\" in fn
            or not os.path.exists(posixpath.join("img", fn))
        ):
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
        if type == "e621":
            return send_file("img/e621_color.svg", mimetype="image/svg+xml")
        fn = f"{config.fs_bases.get(type, '')}/{name}/avatar"
        fn_bck = f"{config.fs_bases.get(type, '')}/{name}/avatar_bck"
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
        fn = f"{config.fs_bases.get(type, '')}/{name}/banner"
        fn_bck = f"{config.fs_bases.get(type, '')}/{name}/banner_bck"
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
    @check_auth()
    def _index():
        return render_template("frame.html", url_base=config.url_base)

    @app.route(posixpath.join("/", config.url_base, "userlist"))
    @check_auth()
    def _userlist():
        page = max(int(request.args.get("p", 1)) - 1, 0)
        sort_type = request.args.get("sort_type", "new")

        query = request.args.get("q", "").strip()
        tab = request.args.get("tab", "all")
        search_bar = render_template("searchbar.html", url_base=config.url_base)
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
                    for u in backend.all_users[sort_type]
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
                users = backend.all_users[sort_type][
                    page * config.items_per_page : (page + 1) * config.items_per_page
                ]
                max_page = ceil(
                    len(backend.all_users[sort_type]) / config.items_per_page
                )
            userlist = render_template(
                "userlist.html",
                users=users,
                url_base=config.url_base,
                tab=tab,
                sort_type=sort_type,
            )
        else:
            all_groups = backend.get_user_groups()
            max_page = ceil(len(all_groups) / config.items_per_page)
            if query:
                all_groups = [g for g in all_groups if query.lower() in g[0].lower()]
            groups = all_groups[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
            users_in_groups = {}
            for group in groups:
                group_name, group_id = group
                user_ids = backend.get_uids_in_group(group_id)
                logger.log(f"Group '{group_name}' has users: {user_ids}", verbose=2)
                users = []
                for user_id in user_ids:
                    user = backend.User(user_id.split("@")[0], user_id.split("@")[1])
                    user.load_from_db()
                    users.append(user)
                users_in_groups[group_name] = users
            userlist = render_template(
                "userlist.html",
                groups=groups,
                users_in_groups=users_in_groups,
                url_base=config.url_base,
                tab=tab,
                sort_type=sort_type,
            )

        return render_template(
            "nav.html",
            current_page=page + 1,
            current_q=query,
            current_url=posixpath.join(
                "/", config.url_base, f"userlist?sort_type={sort_type}"
            ),
            max_page=max_page,
            content=search_bar + userlist,
            section="userlist",
            url_base=config.url_base,
            shorts_decoration="",
            shorts_icon="",
            sort_type=sort_type,
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
        logger.log(
            f"Cuurently scanning user: {backend.current_scan_user}, requested: {name}"
        )
        if backend.current_scan_user == name:
            return render_template(
                "busy.html",
                url_base=config.url_base,
                message=f"Currently scanning {name}'s posts. Please wait.",
            )
        return _timeline(method="user", type_=type, user_name=name)

    @app.route(posixpath.join("/", config.url_base, "add_fav"))
    @check_auth()
    def _add_fav():
        post_id = request.args["post_id"]
        if database.db.query_rows(
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
        posixpath.join("/", config.url_base, "viewer", "<type>", "<name>", "<pid>")
    )
    @check_auth()
    def _viewer(type, name, pid):
        media_index = int(request.args.get("idx", 0))
        user = backend.User(name, type)
        user.load_from_db()
        post = backend.Post(pid, name, type)
        post.load_from_db()
        post.user = user

        _media = database.db.query_rows(
            selected_table="media",
            key="post_id",
            value=pid,
            sort_key=utils.sort_keys["e0"],
            reverse=False,
        )
        media_ids = [m[0] for m in _media if m[5] in (utils.VIDEO, utils.IMAGE)]
        total_media = len(media_ids)
        if len(media_ids) == 0:
            return "No media found for this post.", 404

        print(_media[media_index])
        if media_index < len(_media) and _media[media_index][5] == utils.IMAGE:
            selected_img_file_name = _media[media_index][2]
            selected_img_file_name = posixpath.join(
                "/", config.url_base, "file", type, name, selected_img_file_name
            )
        else:
            selected_img_file_name = ""

        viewer = render_template(
            "viewer.html",
            user=user,
            post=post,
            url_base=config.url_base,
            media_cnt=total_media,
            media_idx=media_index,
            selected_img_file_name=selected_img_file_name,
            quote=quote,
            unquote=unquote,
            progress=(media_index + 1, total_media),
        )
        return viewer

    @app.route(
        posixpath.join("/", config.url_base, "api/media", "<type>", "<name>", "<pid>")
    )
    @check_auth()
    def _api_media(type, name, pid):
        media_index = int(request.args.get("idx", 0))
        user = backend.User(name, type)
        user.load_from_db()
        post = backend.Post(pid, name, type)
        post.load_from_db()
        post.user = user

        media_ids = database.db.query_rows(
            selected_table="media",
            key="post_id",
            value=pid,
            sort_key=utils.sort_keys["e0"],
            reverse=False,
        )
        media_ids = [
            m[0] for m in media_ids if m[5] in (utils.VIDEO, utils.AUDIO, utils.IMAGE)
        ]
        print("media_ids:", media_ids)

        if len(media_ids) < media_index + 1:
            return {"status": "error", "message": "Media index out of range."}
        media_id = media_ids[media_index]
        media = backend.Media(media_id, pid, name, type)
        media.load_from_db()
        mtype = "video" if media.media_type in (utils.VIDEO, utils.AUDIO) else "image"
        data = {
            "status": "success",
            "url": posixpath.join(
                "/", config.url_base, "file", type, name, media.file_name
            ),
            "type": mtype,
            "alt": (
                post.alts[media_index]
                if (media_index < len(post.alts) and media_index != -1)
                else ""
            ),
            "file_size": media.get_size_str(),
        }
        return data

    @app.route(
        posixpath.join("/", config.url_base, "post", "<type>", "<name>", "<post_id>")
    )
    @check_auth()
    def _post(type, name, post_id):
        post_groups = get_post_groups_by_ids(
            [post_id], load_reply=True, load_comments=True
        )
        posts_cnt = sum(len(g) for g in post_groups) if post_groups else 0
        user_obj = backend.User(name, type)
        user_obj.load_from_db()
        _banner_url = posixpath.join("/", config.url_base, "banner", type, name)
        content_frag = render_template(
            "comments.html",
            highlight_post_id=post_id,
            post_groups=post_groups,
            url_base=config.url_base,
            post_found=bool(post_groups),
            detailed_view=True,
            _banner_url=_banner_url,
            quote=quote,
            unquote=unquote,
        )
        nav_kwargs = {
            "content": content_frag,
            "current_page": 1,
            "current_tab": "comments",
            # "sort_type": sort_type,
            "current_url": posixpath.join(
                "/", config.url_base, "comments", type, name, post_id
            ),
            "max_page": 1,
            "section": "comments",
            "url_base": config.url_base,
            "shorts_decoration": "",
            "shorts_icon": "",
            "show_back_btn": True,
            "adjust_padding_top": "4rem",
            "title_str": "Post by " + user_obj.nick if user_obj else "Post",
            "title_secondary_str": f"{posts_cnt} posts in this thread",
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

    @app.route(
        posixpath.join(
            "/", config.url_base, "text", "<type>", "<name>", "<path:filename>"
        )
    )
    @check_auth()
    def _text(type, name, filename):
        file_path = f"{config.fs_bases[type]}/{name}/{filename}"
        if not os.path.exists(file_path):
            logger.log(f"File not found: {file_path}.")
            return "File not found.", 404
        if "/.." in file_path or "../" in file_path or '"' in file_path:
            logger.log(f"Security warning: suspicious path: {file_path}.", type="error")
            return "File not found.", 404
        if utils.media_type_from_extension(filename) != utils.PLAIN_TEXT:
            logger.log(f"File {file_path} is not a plain text file.", type="error")
            return "File not found.", 404
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        content = utils.render_bbcode(content)
        return render_template(
            "text_viewer.html",
            title=filename,
            content=content,
            download_url=posixpath.join(
                "/", config.url_base, "file", type, name, filename
            ),
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

    @app.route(
        posixpath.join("/", config.url_base, "file", "<type>", "<name>", "<path:fn>")
    )
    @check_auth()
    def _file(type, name, fn):
        fn = unquote(fn)
        file_path = f"{config.fs_bases.get(type, '.')}/{name}/{fn}"
        # print(config.fs_bases)
        if not os.path.exists(file_path):
            logger.log(f"File not found: {file_path}.")
            return "File not found.", 404
        if "/.." in file_path or "../" in file_path or '"' in file_path:
            logger.log(f"Security warning: suspicious path: {file_path}.", type="error")
            return "File not found.", 404
        return set_cache_header(send_file(file_path))

    @app.route(
        posixpath.join("/", config.url_base, "upload", "<type>", "<name>", "<post_id>"),
        methods=["GET", "POST"],
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _upload(type, name, post_id):
        if request.method == "GET":
            return render_template(
                "upload.html",
                url_base=config.url_base,
                post_id=post_id,
            )
        else:
            if "url" in request.form and request.form["url"].strip():
                post = backend.Post(post_id, name, type)
                post.load_from_db()
                print(post.text_content)
                print("Before update, post.extra_data:", post.extra_data)
                post.extra_data = post.extra_data or {}
                post.extra_data.setdefault("links", []).append(request.form["url"].strip())
                print("After update, post.extra_data:", post.extra_data)
                post.save_to_db()
                database.db.clear_cache()
                return f"URL {request.form['url'].strip()} added to post {post_id}."
            else:
                # Handle file upload
                if "file" not in request.files:
                    return "No file part in the request.", 400
                file = request.files["file"]
                if file.filename == "":
                    return "No selected file.", 400
                save_path = f"{config.fs_bases.get(type, '.')}/{name}/{post_id.split('@')[0]}_{file.filename}"
                if "/.." in save_path or "../" in save_path or '"' in save_path:
                    logger.log(
                        f"Security warning: suspicious path for upload: {save_path}.",
                        type="error",
                    )
                    return "Invalid file path.", 400
                # check file size, limit to 100MB
                file.seek(0, os.SEEK_END)
                file_length = file.tell()
                file.seek(0)
                if file_length > 1000 * 1024 * 1024:
                    return "File is too large. Max size is 1000MB.", 400
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                file.save(save_path)
                logger.log(f"File uploaded successfully to {save_path}.")
                utils.jobs_queue["maintenance"].insert(
                    0, (f"{name}@{type}", False, False, utils.TYPE_RESCAN)
                )
                return f"File uploaded as {save_path.split('/')[-1]}. It will be processed shortly."

    @app.route(
        posixpath.join("/", config.url_base, "thumb", "<type>", "<name>", "<path:fn>")
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
                set_cache_header(send_file("img/error.png", mimetype="image/jpeg")),
                404,
            )
        if "/.." in path or "../" in path or '"' in path:
            logger.log(
                f"Security warning: suspicious path for thumbnail: {path}.",
                type="error",
            )
            return (
                set_cache_header(send_file("img/error.png", mimetype="image/jpeg")),
                404,
            )
        thumbnail_path = utils.create_thumbnail(path, size)
        if not thumbnail_path or not os.path.exists(thumbnail_path):
            logger.log(f"Thumbnail not found for {path}.")
            return (
                set_cache_header(send_file("img/error.png", mimetype="image/jpeg")),
                404,
            )
        return set_cache_header(send_file(thumbnail_path, mimetype="image/jpeg"))

    @app.route(
        posixpath.join("/", config.url_base, "api", "download"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_download_job():
        data = request.get_json()
        if data.get("url", ""):
            logger.log("Received data:", data)
        rescan = int(data.get("rescan", 0))
        if rescan == 1:
            job_type = utils.TYPE_RESCAN
        elif rescan == 2:
            job_type = utils.TYPE_REBUILD
        else:
            job_type = utils.TYPE_DOWNLOAD
        if "url" in data and data["url"]:
            url = data["url"]
            if "?" in url and not "e621.net" in url:
                url = url.split("?")[0]
            full = data.get("full", False)
            media_only = data.get("media_only", False)
            if re.match(r"[a-zA-Z0-9_.\[\]\(\)-]+@[a-zA-Z]+", url) and job_type in (
                utils.TYPE_RESCAN,
                utils.TYPE_REBUILD,
            ):
                utils.jobs_queue["maintenance"].append((url, False, False, job_type))
                if job_type == utils.TYPE_RESCAN:
                    msg = f"Rescan submitted for user {url}."
                elif job_type == utils.TYPE_REBUILD:
                    msg = f"Rebuild submitted for user {url}."
                logger.log(msg)
                return jsonify(
                    {
                        "status": "ok",
                        "message": msg,
                        "current": utils.get_current_jobs_list(),
                        "queue": utils.get_full_download_queue(),
                    }
                )
            elif (
                ("patreon.com" in url)
                or ("onlyfans.com" in url)
                or ("fanbox.cc" in url)
            ):
                msg = (
                    f"URL not supported. Have you heard of kemono.party / coomer.party?"
                )
                logger.log(msg, type="warning")
                return jsonify(
                    {
                        "status": "ok",
                        "message": msg,
                        "current": utils.get_current_jobs_list(),
                        "queue": utils.get_full_download_queue(),
                    }
                )
            elif not (
                "bsky" in url
                or "x.com" in url
                or "twitter" in url
                or "reddit" in url
                or "furaffinity" in url
                or "kemono." in url
                or "coomer." in url
                or "e621.net" in url
            ):
                msg = f"Invalid URL: {url}\n"
                logger.log(msg)
                return jsonify(
                    {
                        "status": "ok",
                        "message": msg,
                        "current": utils.get_current_jobs_list(),
                        "queue": utils.get_full_download_queue(),
                    }
                )
            if "did:" in url:
                msg = f"Go get the actual bsky handle like 'xxx.bsky.social', {url} won't do.\n"
                logger.log(msg)
                return jsonify(
                    {
                        "status": "ok",
                        "message": msg,
                        "current": utils.get_current_jobs_list(),
                        "queue": utils.get_full_download_queue(),
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
            # Handle Twitter to X transition
            if "twitter.com" in url:
                url = url.replace("twitter.com", "x.com")
            if "/photo/" in url:
                url = re.sub(r"photo/\d+", "", url)
            if "/video/" in url:
                url = re.sub(r"video/\d+", "", url)

            site = utils.identify_site(url)
            # submit job
            if rescan == 1:
                utils.jobs_queue["maintenance"].append(
                    (url, False, False, utils.TYPE_RESCAN)
                )
                msg = "Rescan submitted."
            elif rescan == 2:
                utils.jobs_queue["maintenance"].append(
                    (url, False, False, utils.TYPE_REBUILD)
                )
                msg = "Rebuild submitted."
            elif not (url, full, media_only, job_type) in utils.jobs_queue[site]:
                utils.jobs_queue[site].append((url, full, media_only, job_type))
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
                "current": utils.get_current_jobs_list(),
                "queue": utils.get_full_download_queue(),
            }
        )

    @app.route(posixpath.join("/", config.url_base, "api", "interrupt"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_interrupt():
        logger.log("Interrupt command received via API.")
        run_command.interrupt()
        return {"status": "ok", "message": "Interrupt signal sent to the downloader."}

    @app.route(
        posixpath.join("/", config.url_base, "api", "rename_user"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_rename_user():
        data = request.get_json()
        old_uid = data.get("old_uid", "")
        new_uid = data.get("new_uid", "").lower()
        if not old_uid or not new_uid:
            return jsonify(
                {"status": "error", "message": "Both old_uid and new_uid are required."}
            )
        try:
            utils.rename_user_and_fs_folder(old_uid, new_uid)
            return jsonify(
                {
                    "status": "ok",
                    "message": f"User renamed from {old_uid} to {new_uid} successfully.",
                }
            )
        except Exception as e:
            logger.log(
                f"Error renaming user from {old_uid} to {new_uid}: {e}", type="error"
            )
            logger.log(traceback.format_exc(), type="error")
            return jsonify(
                {"status": "error", "message": f"Error renaming user: {str(e)}"}
            )

    @app.route(posixpath.join("/", config.url_base, "shorts"), methods=["GET"])
    @check_auth()
    def _shorts():
        uid = request.args.get("uid", "")
        user_name, type = uid.split("@") if "@" in uid else (None, None)
        query = request.args.get("q", "")
        return render_template(
            "shorts.html",
            url_base=config.url_base,
            type=type,
            user=user_name,
            uid=uid,
            query=query,
            anchor=request.args.get("anchor", ""),
            current_cnt=0,
            total_cnt=0,
        )

    @app.route(
        posixpath.join("/", config.url_base, "api", "get-a-vid"), methods=["GET"]
    )
    @check_auth()
    def _api_get_a_vid():
        uid = request.args.get("uid", "")
        user_name, type = uid.split("@") if "@" in uid else (None, None)
        anchor = request.args.get("anchor", "")
        query = request.args.get("q", "").strip()
        sort_type = request.args.get("sort_type", "new")
        on_anchor = request.args.get("on_anchor", "0") == "1"

        logger.log(
            f"API get-a-vid called with user='{user_name}', type='{type}', anchor={anchor}, query='{query}', on_anchor={on_anchor}"
        )

        media_id = None
        try:
            if query == "fav":
                media_id, count = db.get_a_video(
                    anchor=anchor, on_anchor=on_anchor, sort_type=sort_type, fav=True
                )
            elif user_name and type:
                media_id, count = db.get_a_video(
                    anchor=anchor, on_anchor=on_anchor, sort_type=sort_type, uid=uid
                )
            elif database.db.get_video_count() == 0:
                media_id, count = None, 0
            else:
                media_id, count = database.db.get_a_video(
                    anchor=anchor, on_anchor=on_anchor, sort_type=sort_type
                )
        except Exception as e:
            logger.log(f"Error in get_a_video: {e}", type="error")
            logger.log(traceback.format_exc(), type="error")
            media_id, count = None, 0
            if args.debug:
                return jsonify(
                    {"status": "error", "message": f"Error: {traceback.format_exc()}"}
                )
        if not media_id:
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
        post.user = user
        data = {
            "status": "ok",
            "message": "",
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
                database.db.query_rows(
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
            current_url="",
            max_page=0,
            content=setting_frag,
            section="settings",
            url_base=config.url_base,
            shorts_decoration="",
            shorts_icon="",
            adjust_padding_top="0.5rem",
        )

    @app.route(
        posixpath.join("/", config.url_base, "test"),
        methods=["GET", "POST"],
    )
    @check_auth()
    def _test_page():
        if request.method == "GET":
            return render_template(
                "test.html",
                url_base=config.url_base,
            )
        else:
            data = request.get_json()
            test_cat = data.get("test_cat", "")
            logger.log(f"Received test data: {data}", verbose=3)
            try:
                if test_cat == "embbed_hyperlink":
                    text = data.get("text", "")
                    type_ = data.get("type", "x")
                    rendered = utils.embed_hyperlink(type_, text)
                    return jsonify({"status": "ok", "result": rendered})
                elif test_cat == "bbcode":
                    text = data.get("text", "")
                    rendered = utils.render_bbcode(text)
                    return jsonify({"status": "ok", "result": rendered})
                elif test_cat == "tokenize_text":
                    text = data.get("text", "")
                    tokens = utils.tokenize_text(text)
                    return jsonify({"status": "ok", "result": tokens})
                elif test_cat == "search_suggestion":
                    query = data.get("text", "")
                    suggestions = utils.get_search_suggestions(query)
                    return jsonify({"status": "ok", "result": suggestions})
                else:
                    return jsonify(
                        {"status": "error", "message": "Unknown test category."}
                    )
            except Exception as e:
                logger.log(f"Error in test API: {e}", type="error")
                logger.log(traceback.format_exc(), type="error")
                return jsonify(
                    {"status": "error", "message": f"Error: {traceback.format_exc()}"}
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
            for row in database.db.query_rows(
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
        pairs = data.get("pairs", [])
        print("Ungrouping users with pairs:", pairs)
        for pair in pairs:
            uid = pair.get("uid", "")
            group_id = pair.get("group_id", "")
            backend.remove_user_from_group(uid, group_id)
        return {"status": "ok", "message": "Users removed from group."}

    @app.route(
        posixpath.join("/", config.url_base, "api", "ungroup"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_ungroup():
        data = request.get_json()
        group_id = data.get("group_id", "")
        uids = backend.get_uids_in_group(group_id)
        for uid in uids:
            backend.remove_user_from_group(uid, group_id)
        logger.log(f"Users {uids} have been removed from group {group_id} by admin.")
        return {
            "status": "ok",
            "message": f"Users {uids} have been removed from group {group_id}.",
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

    @app.route(
        posixpath.join("/", config.url_base, "api", "upload_cookies"), methods=["POST"]
    )
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_upload_cookies():
        file = request.files["cookies"]
        type = request.form.get("type", "")
        if type not in ["x", "bsky", "reddit", "fa", "e621"]:
            return {"status": "error", "message": "Invalid type."}
        if type == "x":
            save_path = "x.com_cookies.txt"
        elif type == "fa":
            save_path = "fadl/cookies.txt"
        elif type == "e621":
            save_path = "e6dl/cookies.txt"
        else:
            return {
                "status": "error",
                "message": "Cookie upload not supported for this type.",
            }
        # check length of file, should not be too large
        file.seek(0, os.SEEK_END)
        file_length = file.tell()
        file.seek(0)
        if file_length > 1024 * 1024:
            return {"status": "error", "message": "File too large."}
        utils.copy_ua_from_request()
        file.save(save_path)
        logger.log(f"Cookies for {type} uploaded and saved to {save_path} by admin.")
        return {"status": "ok", "message": f"Cookies for {type} uploaded successfully."}

    @app.route(posixpath.join("/", config.url_base, "api", "rescan"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_rescan():
        full = request.args.get("full", "0") == "1"

        def delayed_rescan():
            logger.log("Rescan will start in 5 seconds...")
            time.sleep(5)
            backend.scan_for_users("x")
            backend.scan_for_users("bsky")
            backend.scan_for_users("reddit")
            backend.scan_for_users("fa")
            backend.scan_for_users("patreon")
            backend.scan_for_users("e621")
            if full:
                backend.scan_for_posts("x")
                backend.scan_for_posts("bsky")
                backend.scan_for_posts("reddit")
                backend.scan_for_posts("fa")
                backend.scan_for_posts("patreon")
                backend.scan_for_posts("e621")
            backend.scan_custom_user(scan_posts=not full)
            database.db.commit()
            backend.all_users = backend.get_users()
            logger.log("Rescan completed.")

        Thread(target=delayed_rescan, daemon=True).start()
        return {"status": "ok", "message": "Rescan started."}

    @app.route(posixpath.join("/", config.url_base, "api", "inspect_post/<post_id>"))
    @check_auth()
    def _api_inspect_post(post_id):
        post_rows = database.db.query_rows(
            selected_table="posts", key="post_id", value=post_id, ignore_cache=True
        )
        if not post_rows:
            return {"status": "error", "message": f"Post {post_id} not found."}
        post_row = post_rows[0]
        return {"status": "ok", "message": "", "post": post_row}

    @app.route(posixpath.join("/", config.url_base, "cache_proxy", "<path:subpath>"))
    @check_auth()
    def cache_proxy(subpath):
        if not utils.check_allowed_to_proxy(subpath):
            return "Not allowed.", 403
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
        elif "patreon.com" in subpath:
            subpath = quote(subpath)
        else:
            filename = utils.filter_ascii("_".join(subpath.split("/")))
        subpath = "https://" + subpath
        cache_path = os.path.join("tmp/.cached", filename)
        if os.path.exists(cache_path):
            logger.log(f"Serving from cache: {cache_path}", verbose=1)
            return set_cache_header(send_file(cache_path))
        else:
            logger.log(f"Proxying request for: {subpath}", type="attention")
            os.makedirs("tmp/.cached", exist_ok=True)
            logger.log(f"Fetching from remote: {subpath}", type="attention")
            try:
                r = utils.get(subpath)
                if r.status_code != 200:
                    raise Exception(
                        f"Failed to fetch {subpath}, status code: {r.status_code}"
                    )
                bin_ = r.content
                if len(bin_) < 100:
                    raise Exception("Too small")
                with open(cache_path, "wb") as f:
                    f.write(bin_)
                logger.log(f"Cached to: {cache_path}")
            except Exception as e:
                logger.log(e, type="error")
                with open("img/error.png", "rb") as f1:
                    with open(cache_path, "wb") as f2:
                        f2.write(f1.read())
                logger.log(
                    f"Failed to get {subpath}, use dummy image for {cache_path}",
                    type="error",
                )
            return set_cache_header(send_file(cache_path))

    @app.route(posixpath.join("/", config.url_base, "api", "call"))
    @check_auth(required_role=utils.ROLE_ADMIN)
    def _api_call():
        func_name = request.args.get("func", "")
        args = request.args.get("args", "")
        args = args.split(",") if args else []
        if not func_name:
            return {"status": "error", "message": "Function name is required."}
        if func_name == "build_search_suggestions":
            Thread(target=utils.build_search_suggestions, daemon=True).start()
            return {"status": "ok", "message": "Search suggestions rebuilt."}
        else:
            return {"status": "error", "message": f"Unknown function: {func_name}"}

    @app.route(posixpath.join("/", config.url_base, "api", "search_suggestions"))
    @check_auth()
    def _api_search_suggestions():
        q = request.args.get("q", "")
        q = q.split(", ")[-1].lower().strip()
        logger.log(f"Search suggestions requested for query: '{q}'", verbose=0)
        suggestions = utils.get_search_suggestions(q)
        return {"status": "success", "message": "", "suggestions": suggestions}

    return app


def init(skip_scan, skip_scan_users):
    utils.current_status = utils.SCANNING
    logger.log("Starting initial scan of users and posts...")
    if not skip_scan_users:
        backend.scan_for_users("x")
        backend.scan_for_users("bsky")
        backend.scan_for_users("reddit")
        backend.scan_for_users("fa")
        backend.scan_for_users("patreon")
        backend.scan_for_users("e621")
    if not skip_scan:
        backend.scan_for_posts("x")
        backend.scan_for_posts("bsky")
        backend.scan_for_posts("reddit")
        backend.scan_for_posts("fa")
        backend.scan_for_posts("patreon")
        backend.scan_for_posts("e621")
    backend.scan_custom_user(scan_posts=not skip_scan)
    database.db.commit()
    backend.all_users = backend.get_users()
    logger.log("Scan finished.")
    utils.current_status = utils.RUNNING

    if args.update_daemon:
        logger.log("Starting update daemon...")
        Thread(target=utils.update_daemon, daemon=True).start()

    utils.global_running_flag = True
    for site in list(config.fs_bases.keys()) + ["maintenance"]:
        if not site in utils.jobs_queue:
            continue
        if not site in utils.running_workers:
            worker = utils.DownloadWorker(db, site)
            worker.setDaemon(True)
            worker.start()
            utils.running_workers.add(site)
        else:
            logger.log(f"Worker for {site} already running.")
            logger.log(
                f"Probably you are using hot reload feature of flask, which will restart the app but not the whole process, so the worker thread is still running in background. No need to worry about it."
            )
    logger.log("Download worker started.")


def shutdown_cleanup():
    print("Shutting down, performing cleanup...")
    utils.global_running_flag = False
    database.db.commit()
    logger.log("Cleanup done. Goodbye!")


def signal_handler(signal, frame):
    shutdown_cleanup()
    sys.exit(0)


def wsgi_app(skip_scan=False, skip_scan_users=False, config_file="config.json"):
    print("Starting app with wsgi_app()")
    config.read_config(config_file)
    init(skip_scan, skip_scan_users)
    app = build_app()
    logger.log(f"app is ready at: http://{config.host}:{config.port}{config.url_base}")
    return app


def delayed_stop():
    def stop():
        database.db.commit()
        time.sleep(1)  # Wait a moment to ensure the response is sent before stopping
        os.kill(os.getpid(), signal.SIGTERM)

    Thread(target=stop, daemon=True).start()


db = database.Database("data.db", "fav.db")
db.prepare_db()
database.set_db(db)

if args.monitor_timeline:
    logger.log("Initializing bsky monitor...")
    bsky_monitor = live_timeline_monitor.BlueskyTimelineMonitor()
    if bsky_monitor.start() == 0:
        logger.log("Bluesky monitor started.")
    else:
        logger.log("Failed to start Bluesky monitor.", type="error")

logger.log("Ready.")

if __name__ == "__main__":
    init(args.skip_scan, args.skip_scan_users)
    app = build_app()
    logger.log(f"app is ready at: http://{config.host}:{config.port}{config.url_base}")
    app.run(host=config.host, port=config.port, debug=args.debug)
    shutdown_cleanup()
    sys.exit(0)
else:
    signal.signal(signal.SIGTERM, signal_handler)  # Handle SIGTERM
