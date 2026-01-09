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
import os, time
import re
import natsort
from urllib.parse import unquote, quote
import posixpath
from math import ceil, floor
from random import sample, randint
from threading import Thread
import argparse
import requests
import signal
import traceback
import sys

import config, backend, utils, logger, run_command

parser = argparse.ArgumentParser(description="Check for command-line options.")

parser.add_argument(
    "--debug", action="store_true", help="Include this option to enable 'debug'."
)

parser.add_argument("--skip-scan", action="store_true", help="Skip startup scan.")
parser.add_argument("--update-daemon", action="store_true", help="Regularly update.")

args, unknown = parser.parse_known_args()
args.debug = bool(args.debug)
backend.debug_mode = args.debug
if args.debug:
    logger.VERBOSE_LEVEL = 1
    logger.log("Debug mode enabled.", verbose=1)
args.skip_scan = bool(args.skip_scan)

# temporarily enable debug mode
# args.debug = True

app = Flask(__name__)


def set_cache_header(response):
    if not args.debug:
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.after_request
def set_csp_header(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route(posixpath.join("/", config.url_base, "mt.webmanifest"))
def _webmanifest():
    with open("mt.webmanifest", "r") as f:
        return render_template_string(f.read(), url_base=config.url_base)


if config.url_base.strip("/") != "":

    @app.route("/")
    def _root():
        return redirect(posixpath.join("/", config.url_base, "tl"))


@app.route(posixpath.join("/", config.url_base, "js", "<fn>"))
def _js(fn):
    return set_cache_header(send_from_directory("js", fn))


@app.route(posixpath.join("/", config.url_base, "css", "<fn>"))
def _css(fn):
    return set_cache_header(send_from_directory("css", fn))


@app.route(posixpath.join("/", config.url_base, "img", "<fn>"))
def _img(fn):
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
        user.load_from_db(db)
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
            logger.log("Downloading avatar:", avatar_url, "for", name, type="attention")
            r = requests.get(avatar_url, headers=utils.headers)
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
        user.load_from_db(db)
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
            logger.log("Downloading banner:", banner_url, "for", name, type="attention")
            r = requests.get(banner_url, headers=utils.headers)
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
            return set_cache_header(send_file("img/empty.png", mimetype="image/jpeg"))
    return set_cache_header(send_file(fn, mimetype="image/jpeg"))


@app.route(posixpath.join("/", config.url_base + "/"))
@app.route(posixpath.join("/", config.url_base))
def _index():
    return redirect(posixpath.join("/", config.url_base, "tl"))


@app.route(posixpath.join("/", config.url_base, "userlist"))
def _userlist():
    if "p" in request.args:
        page = int(request.args["p"]) - 1
        page = max(0, page)
    else:
        page = 0
    if "q" in request.args:
        query = request.args["q"]
    else:
        query = ""
    if query:
        all_users = [
            u
            for u in backend.all_users
            if query.lower() in u.nick.lower()
            or query.lower() in u.user_name.lower()
            or query.lower() in u.description.lower()
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
    seach_bar = render_template("searchbar.html", url_base=config.url_base)
    userlist = render_template("userlist.html", users=users, url_base=config.url_base)
    return render_template(
        "nav.html",
        current_page=page + 1,
        current_q=query,
        current_url=posixpath.join("/", config.url_base, "userlist"),
        max_page=max_page,
        content=seach_bar + userlist,
        section="users",
        url_base=config.url_base,
    )


tl_current_sort = "new"
tl_current_page = {
    "new": 0,
    "top": 0,
    "random": 0,
}


def get_posts(method="tl", query="", sort_="new", page=0, user_name="", type_=""):
    """
    A helper function to get posts for timeline.
    :param method: "tl", "fav", or "user"
    :param query: search query
    :param sort_: "new", "top", or "random" (only used for "tl" method)
    :param page: page number (0-indexed)
    :param user_name: user name (only used for "user" method)
    :param type_: user type like "x", "bsky", "reddit", "fa" (only used for "user" method)

    :return: tuple of (list of post IDs for the page, total post count)
    """
    global tl_current_sort, tl_current_page

    if method == "tl":
        if query:
            sorted_posts_id = db.query_post_by_text(query)
            all_post_count = len(sorted_posts_id)
            sorted_posts_id = sorted_posts_id[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
            sorted_posts_id = [i[0] for i in sorted_posts_id][::-1]
        else:
            all_post_count = len(backend.cache_all_posts_id)
            if sort_ == "new":
                sorted_posts_id = backend.cache_all_posts_id[
                    page * config.items_per_page : (page + 1) * config.items_per_page
                ]
            elif sort_ == "top":
                sorted_posts_id = backend.cache_all_posts_id_top[
                    page * config.items_per_page : (page + 1) * config.items_per_page
                ]
            elif sort_ == "random":
                sorted_posts_id = backend.cache_all_posts_id_random[
                    page * config.items_per_page : (page + 1) * config.items_per_page
                ]
            else:
                return [], 0
            sorted_posts_id = [i[0] for i in sorted_posts_id][::-1]
        return sorted_posts_id, all_post_count

    elif method == "fav":
        sorted_posts_id = backend.get_fav(db)
        all_post_count = len(sorted_posts_id)
        sorted_posts_id = [i[0] for i in sorted_posts_id if i[0]][::-1]
        sorted_posts_id = sorted_posts_id[
            page * config.items_per_page : (page + 1) * config.items_per_page
        ][::-1]
        return sorted_posts_id, all_post_count

    elif method == "user":
        if not user_name or not type_:
            return [], 0
        uid = f"{user_name}@{type_}"
        all_rows = db.query_rows(
            selected_db="posts", key="uid", value=uid, sort_key=lambda x: x[4]
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


def _timeline(method="tl", type_="", user_name=""):
    """
    Unified timeline function that handles overall timeline, favorites, and user timeline.
    :param method: "tl", "fav", or "user"
    :param type_: user type like "x", "bsky", "reddit", "fa" (only used for "user" method)
    :param user_name: user name (only used for "user" method)
    """
    global tl_current_sort, tl_current_page

    if utils.busy_flag:
        return render_template("busy.html", url_base=config.url_base)

    # Parse common request args
    if "p" in request.args:
        page = int(request.args["p"]) - 1
        page = max(0, page)
    else:
        page = 0

    if "tab" in request.args:
        tab = request.args["tab"]
    else:
        tab = "posts"

    if "q" in request.args:
        query = request.args["q"].strip()
    else:
        query = ""

    # Sort is only used for "tl" method
    if "sort" in request.args:
        sort_ = request.args["sort"]
        tl_current_sort = sort_
    else:
        sort_ = tl_current_sort

    # For "tl" method, handle page persistence per sort type
    if method == "tl":
        if "p" in request.args:
            tl_current_page[tl_current_sort] = page
        elif not query:
            page = tl_current_page[tl_current_sort]

    # For "user" method, prepare user object
    user_obj = None
    if method == "user":
        user_name = user_name.lower()
        user_obj = backend.User(user_name, type_)
        user_obj.load_from_db(db)
        if not user_obj.url:
            user_obj.concat_url()

    # Get posts based on method
    if method == "tl":
        sorted_posts_id, all_post_count = get_posts(
            method="tl", query=query, sort_=sort_, page=page
        )
        if not query and sort_ not in ("new", "top", "random"):
            return "Invalid sort type."
    elif method == "fav":
        try:
            sorted_posts_id, all_post_count = get_posts(method="fav", page=page)
        except ValueError:
            return "Not enough posts. Download more and come back later."
    elif method == "user":
        sorted_posts_id, all_post_count = get_posts(
            method="user", page=page, user_name=user_name, type_=type_
        )
    else:
        return "Invalid method."

    # Handle media tab for fav and user methods
    if tab == "media" and method in ("fav", "user"):
        # Re-fetch with adjusted pagination for media tab
        if method == "fav":
            fav_posts = backend.get_fav(db)
            all_post_count = len(fav_posts)
            fav_post_ids = [i[0] for i in fav_posts if i[0]][::-1]
            sorted_posts_id = fav_post_ids[
                page
                * config.items_per_page
                * 2 : (page + 1)
                * config.items_per_page
                * 2
            ]
        elif method == "user":
            uid = f"{user_name}@{type_}"
            all_rows = db.query_rows(
                selected_db="media", key="uid", value=uid, sort_key=lambda x: x[5]
            )
            all_post_count = len(all_rows)

            media_entries = dict()
            sorted_media_id = []
            for row in all_rows[
                page
                * config.items_per_page
                * 2 : (page + 1)
                * config.items_per_page
                * 2
            ]:
                media_id = row[0]
                post_id = row[1]
                media = backend.Media(media_id, post_id, user_name, type_, "")
                media.load_from_db(db)
                media_entries[media_id] = media
                sorted_media_id.append(media_id)

            timeline_content = render_template(
                "mediagrid.html",
                media_entries=media_entries,
                sorted_media_id=sorted_media_id,
                page=page,
                items_per_page=config.items_per_page * 2,
                user_name=user_name,
                type=type_,
                users={f"{user_name}": user_obj},
                url_base=config.url_base,
                page_url=f"{config.url_base}/user/{type_}/{user_name}",
            )
            max_page = ceil(all_post_count / config.items_per_page / 2)

            userheader = render_template(
                "userheader.html",
                type=type_,
                user=user_obj,
                url_base=config.url_base,
                posts_cnt=all_post_count,
            )
            return render_template(
                "nav.html",
                content=userheader + timeline_content,
                section="users",
                current_page=page + 1,
                current_url=posixpath.join(
                    "/", config.url_base, "user", type_, user_name
                )
                + "?tab="
                + tab,
                max_page=max_page,
                alt_home_icon=posixpath.join(
                    "/", config.url_base, "avatar", type_, user_name
                ),
                title=f"{user_obj.nick} (@{user_name}) - {type_}",
                url_base=config.url_base,
                user=user_obj,
                adjust_padding_top=True,
            )

        # Handle media tab for fav
        if method == "fav":
            media_entries = dict()
            users = dict()
            sorted_media_id = []
            for post_id in sorted_posts_id:
                if post_id in ("redgifs",):
                    continue
                post = backend.Post(post_id, None, None)
                if not post.load_from_db(db):
                    logger.log(f"Post [{post_id}] not found.")
                    continue
                for row in db.query_rows(
                    selected_db="media", key="post_id", value=post_id
                ):
                    media_id = row[0]
                    sorted_media_id.append(media_id)
                    media = backend.Media(media_id, post_id, None, post.type, post.time)
                    media.load_from_db(db)
                    media_entries[media_id] = media
                if post.user_name not in users:
                    user = backend.User(post.user_name, post.type)
                    user.load_from_db(db)
                    users[post.user_name] = user

            timeline_content = render_template(
                "mediagrid.html",
                media_entries=media_entries,
                sorted_media_id=sorted_media_id,
                page=page,
                items_per_page=config.items_per_page * 2,
                user_name="",
                type=type,
                users=users,
                url_base=config.url_base,
                page_url=f"{config.url_base}/fav",
            )
            max_page = ceil(all_post_count / config.items_per_page / 2)

            return render_template(
                "nav.html",
                current_page=page + 1,
                current_url=posixpath.join("/", config.url_base, "fav") + "?tab=" + tab,
                max_page=max_page,
                content=timeline_content,
                section="fav",
                url_base=config.url_base,
                adjust_padding_top=True,
            )

    # Load posts and media for posts tab
    posts = dict()
    external_posts = dict()
    media_entries = dict()
    users = dict()
    existing_sorted_posts_id = []

    cnt = 0
    while cnt < len(sorted_posts_id):
        if len(sorted_posts_id) > config.items_per_page * 5:
            logger.log(
                f"Warning: Large number of posts to load: {len(sorted_posts_id)}",
                type="warning",
            )
        if len(sorted_posts_id) > config.items_per_page * 10:
            logger.log(
                f"Error: Too many posts to load: {len(sorted_posts_id)}, there might be a loop in replies. Stopping further loading.",
                type="error",
            )
            break
        post_id = sorted_posts_id[cnt]
        cnt += 1
        if post_id in posts:
            post = posts[post_id]
            existing_sorted_posts_id.append(post_id)
            # load reply info here, so that replies of duplicate posts are also handled
            if post.isreply and post.reply_to and method != "fav":
                reply_post_id, reply_user_name = post.reply_to.split("@")
                sorted_posts_id.insert(cnt, reply_post_id)
                external_posts[reply_post_id] = (post.type, reply_user_name)
            continue
        if post_id in ("redgifs",):
            continue

        post = backend.Post(post_id, None, None)
        if not post.load_from_db(db):
            if method == "tl" or method == "user":
                post.isplaceholder = True
                post.type, post.user_name = external_posts.get(post_id, ("", ""))
                post.concat_url()
                print("guessed url for external post:", post.url)
            elif method == "fav":
                logger.log(f"Post [{post_id}] not found.")
                post.user_name = "None"
                post.text_content = (
                    f"This post is missing from file system. [{post_id}]"
                )
                post.fav = True
            else:
                post.user_name = "None"
                post.text_content = f"HOW DID YOU EVEN GET HERE? [{post_id}]"

        # Load reply info
        if post.isreply and post.reply_to:
            print("post", post.post_id, "is a reply to", post.reply_to)
            reply_post_id, reply_user_name = post.reply_to.split("@")
            sorted_posts_id.insert(cnt, reply_post_id)
            external_posts[reply_post_id] = (post.type, reply_user_name)

        post.init_embed(db)
        posts[post_id] = post
        existing_sorted_posts_id.append(post_id)

        # Load media for post
        for row in db.query_rows(selected_db="media", key="post_id", value=post_id):
            media_id = row[0]
            media = backend.Media(
                media_id, post_id, post.user_name, post.type, post.time
            )
            media.load_from_db(db)
            if post_id not in media_entries:
                media_entries[post_id] = [media]
            else:
                media_entries[post_id].append(media)

        if post_id in media_entries and len(media_entries[post_id]) > 1:
            media_entries[post_id] = natsort.natsorted(
                media_entries[post_id], key=lambda x: x.media_id
            )

        # Load user info
        if post.user_name not in users:
            user = backend.User(post.user_name, post.type)
            user.load_from_db(db)
            if method == "fav" and post.user_name == "None":
                user.nick = "None"
            users[post.user_name] = user

    # For user method, ensure user is in users dict
    if method == "user" and user_name not in users:
        users[user_name] = user_obj

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
        show_sort_type = True
        show_media_toggle = False
        section = "tl"
        final_sorted_posts_id = sorted_posts_id
    elif method == "fav":
        page_url = f"{config.url_base}/fav"
        show_sort_type = False
        show_media_toggle = True
        section = "fav"
        final_sorted_posts_id = sorted_posts_id
    else:  # user
        page_url = f"{config.url_base}/user/{type_}/{user_name}"
        show_sort_type = False
        show_media_toggle = True
        section = "users"
        final_sorted_posts_id = sorted_posts_id

    # Render timeline
    timeline_content = render_template(
        "timeline.html",
        section=section,
        posts=posts,
        media_entries=media_entries,
        sorted_posts_id=final_sorted_posts_id,
        page=page,
        items_per_page=config.items_per_page,
        user_name=user_name if method == "user" else "",
        type=type_ if method == "user" else ("tl" if method == "tl" else ""),
        users=users,
        url_base=config.url_base,
        page_url=page_url,
        show_sort_type=show_sort_type,
        sort_type=sort_ if method == "tl" else None,
        show_media_toggle=show_media_toggle,
    )

    max_page = ceil(all_post_count / config.items_per_page)

    # Build content with optional headers
    if method == "tl":
        search_bar = render_template("searchbar.html", url_base=config.url_base)
        content = search_bar + timeline_content
        current_url = (
            posixpath.join("/", config.url_base, "tl")
            + "?tab="
            + tab
            + "&sort="
            + sort_
        )
    elif method == "fav":
        content = timeline_content
        current_url = posixpath.join("/", config.url_base, "fav") + "?tab=" + tab
    else:  # user
        userheader = render_template(
            "userheader.html",
            type=type_,
            user=user_obj,
            url_base=config.url_base,
            posts_cnt=all_post_count,
        )
        content = userheader + timeline_content
        current_url = (
            posixpath.join("/", config.url_base, "user", type_, user_name)
            + "?tab="
            + tab
        )

    # Build nav template kwargs
    nav_kwargs = {
        "content": content,
        "current_page": page + 1,
        "current_url": current_url,
        "max_page": max_page,
        "section": section,
        "url_base": config.url_base,
    }

    if method == "tl":
        nav_kwargs["current_q"] = query
    elif method == "fav":
        nav_kwargs["adjust_padding_top"] = True
    else:  # user
        nav_kwargs["adjust_padding_top"] = True
        nav_kwargs["alt_home_icon"] = posixpath.join(
            "/", config.url_base, "avatar", type_, user_name
        )
        nav_kwargs["title"] = f"{user_obj.nick} (@{user_name}) - {type_}"
        nav_kwargs["user"] = user_obj

    return render_template("nav.html", **nav_kwargs)


@app.route(posixpath.join("/", config.url_base, "tl"))
def _timeline_all():
    return _timeline(method="tl")


@app.route(posixpath.join("/", config.url_base, "fav"))
def _timeline_fav():
    return _timeline(method="fav")


@app.route(posixpath.join("/", config.url_base, "user", "<type>", "<name>"))
def _timeline_user(type, name):
    return _timeline(method="user", type_=type, user_name=name)


@app.route(posixpath.join("/", config.url_base, "add_fav"))
def _add_fav():
    post_id = request.args["post_id"]
    if db.query_rows(
        selected_db="fav", key="post_id", value=post_id, ignore_cache=True
    ):
        logger.log("remove favorite", post_id)
        backend.remove_favorite(db, post_id)
        return {
            "result": "removed",
        }
    else:
        logger.log("add favorite", post_id)
        backend.add_favorite(db, post_id)
        return {
            "result": "added",
        }


@app.route(
    posixpath.join("/", config.url_base, "card", "<type>", "<name>", "<filename>")
)
def _card(type, name, filename):
    if type in ["x", "bsky", "reddit"]:
        media_id = filename.split(".")[0]
    elif type == "fa":
        media_id = filename
    else:
        return "Invalid type."
    media = backend.Media(media_id, None, name, type, "")
    media.load_from_db(db)
    user = backend.User(name, type)
    user.load_from_db(db)
    post = backend.Post(media.post_id, name, type)
    post.load_from_db(db)
    card = render_template(
        "card.html", media=media, user=user, post=post, url_base=config.url_base
    )
    return card


@app.route(
    posixpath.join("/", config.url_base, "ruffle", "<type>", "<name>", "<filename>")
)
def _ruffle(type, name, filename):
    return render_template(
        "ruffle.html",
        type=type,
        user_name=name,
        file_name=filename,
        url_base=config.url_base,
    )


@app.route(posixpath.join("/", config.url_base, "download"))
def _download():
    url = ""
    # logger.log('-'*10,request.args)
    # logger.log('+'*10,"url" in request.args)
    if "url" in request.args:
        url = request.args["url"]
    download = render_template(
        "download.html",
        default_input=url,
        msg=utils.current_url,
        queue="",
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
    )


@app.route(posixpath.join("/", config.url_base, "file", "<type>", "<name>", "<fn>"))
def _file(type, name, fn):
    fn = unquote(fn)
    # logger.log(f"Requesting file: {type}/{name}/{fn}")
    return set_cache_header(send_from_directory(config.fs_bases[type], f"{name}/{fn}"))


@app.route(posixpath.join("/", config.url_base, "thumb", "<type>", "<name>", "<fn>"))
def _thumb(type, name, fn):
    fn = unquote(fn)
    size = config.thubnail_size
    if "size" in request.args:
        size = int(request.args["size"])
        size = min(max(size, 32), 2500)
    path = f"{config.fs_bases[type]}/{name}/{fn}"
    thumbnail_path = utils.create_thumbnail(path, size)
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        logger.log(f"Thumbnail not found for {path}.")
        return set_cache_header(send_file("img/error.jpg", mimetype="image/jpeg"))
    return set_cache_header(send_file(thumbnail_path, mimetype="image/jpeg"))


@app.route(posixpath.join("/", config.url_base, "view", "<type>", "<name>", "<fn>"))
def _view(type, name, fn):
    return render_template(
        "viewer.html",
        type=type,
        user_name=name,
        file_name=fn,
        isvideo=fn.endswith(".mp4") or fn.endswith(".webm"),
        url_base=config.url_base,
    )


@app.route(posixpath.join("/", config.url_base, "add"), methods=["POST"])
def _add_download_job():
    data = request.get_json()
    # logger.log("Received data:", data)
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
                {"msg": msg, "current": utils.current_url, "queue": utils.download_jobs}
            )
        if "did:" in url:
            msg = f"Go get the actual bsky handle like 'xxx.bsky.social', {url} won't do.\n"
            logger.log(msg)
            return jsonify(
                {"msg": msg, "current": utils.current_url, "queue": utils.download_jobs}
            )
        if re.match("\w+\.bsky\.social", url):
            url = "https://bsky.app/profile/" + utils.filter_ascii(url).strip()
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
        if not (url, full, media_only) in utils.download_jobs:
            # utils.download_jobs.append((url, full, media_only))
            utils.download_jobs.insert(0, (url, full, media_only))
            msg = f"Added {url} to download queue.\n"
        else:
            msg = f"{url} already in download queue.\n"
        logger.log(msg)
    else:
        msg = "Enter your url above.\n"
    return jsonify(
        {"msg": msg, "current": utils.current_url, "queue": utils.download_jobs}
    )


@app.route(posixpath.join("/", config.url_base, "api", "interrupt"))
def _api_interrupt():
    run_command.interrupt()
    return {"status": "interrupted"}


@app.route(posixpath.join("/", config.url_base, "shorts"), methods=["GET"])
def _shorts():
    if utils.busy_flag:
        return render_template("busy.html", url_base=config.url_base)
    type = request.args.get("type", "")
    user_name = request.args.get("user", "")
    uid = f"{user_name}@{type}"
    query = request.args.get("q", "")
    if user_name:
        if not uid in backend.cache_user_media_id:
            logger.log(f"cache miss for {user_name}, building cache...")
            # Need to find media for this user across all sources
            media_ids = db.raw_query(
                f"SELECT media_id, time, file_name FROM media WHERE uid = '{user_name}@{type}'"
            )
            media_ids = [
                (i[0], i[1])
                for i in media_ids
                if i[2].split(".")[-1] in backend.valid_video_types
            ]
            if len(media_ids) > 0:
                media_ids = sorted(media_ids, key=lambda s: s[1], reverse=True)
                media_ids = [i[0] for i in media_ids]
                backend.cache_user_media_id[uid] = media_ids
            total_cnt = len(media_ids)
        else:
            total_cnt = len(backend.cache_user_media_id[uid])
        return render_template(
            "shorts.html",
            url_base=config.url_base,
            type=type,
            user=user_name,
            query=query,
            total_cnt=total_cnt,
            current_cnt=0,
        )
    elif query:
        media_ids = db.query_media_by_text(query)
        total_cnt = len(media_ids)
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
        idx = randint(0, len(backend.cache_all_media_id))
        return render_template(
            "shorts.html",
            url_base=config.url_base,
            type=type,
            current_cnt=idx,
            user=user_name,
            query=query,
            total_cnt=0,
        )


@app.route(posixpath.join("/", config.url_base, "get-a-vid"), methods=["GET"])
def _get_a_vid():
    user_name = request.args.get("user", "")
    type = request.args.get("type", "")
    uid = f"{user_name}@{type}"
    idx = int(request.args.get("idx", 0))
    query = request.args.get("q", "").strip()
    if user_name:
        # get a media_id from user by order
        if not uid in backend.cache_user_media_id:
            logger.log(f"[shorts] Cache miss for {user_name}, building cache...")
            # Need to find the uid for this user - try to get type from most recent media
            media_rows = db.raw_query(
                f"SELECT media_id, time, file_name, type FROM media WHERE uid = '{uid}'"
            )
            if not media_rows:
                return {
                    "error": f"No media found for user {user_name}.",
                }
            media_ids = [
                (i[0], i[1])
                for i in media_rows
                if i[2].split(".")[-1] in backend.valid_video_types
            ]
            if len(media_ids) > 0:
                media_ids = sorted(media_ids, key=lambda s: s[1], reverse=True)
                media_ids = [i[0] for i in media_ids]
                backend.cache_user_media_id[uid] = media_ids
            if not media_ids:
                return {
                    "error": f"No video found for user {uid}.",
                }
            backend.cache_user_media_id[uid] = media_ids
        else:
            logger.log(f"[shorts] Cache hit for {uid}", verbose=2)
            media_ids = backend.cache_user_media_id[uid]
        idx = idx % len(media_ids)
        media_id = media_ids[idx]
    elif query:
        media_ids = db.query_media_by_text(query)
        idx = idx % len(media_ids)
        media_id = media_ids[idx]
    else:
        if len(backend.cache_all_media_id) == 0:
            return {
                "error": f"No video found.",
            }
        media_id = backend.cache_all_media_id[idx % len(backend.cache_all_media_id)]
    media = backend.Media(media_id, None, None, "", "")
    media.load_from_db(db)
    post = backend.Post(media.post_id, None, None)
    post.load_from_db(db)
    user = backend.User(post.user_name, post.type)
    user.load_from_db(db)
    data = {
        "url": posixpath.join(
            "/", config.url_base, "file", post.type, post.user_name, media.file_name
        ),
        "preview": posixpath.join(
            "/", config.url_base, "thumb", post.type, post.user_name, media.file_name
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
            db.query_rows(selected_db="fav", key="post_id", value=post.post_id)
        ),
        "post_url": post.url,
        "user_url": f"{config.url_base}/user/{post.type}/{post.user_name}",
        "time": post.time,
        "type": post.type,
    }
    return jsonify(data)


@app.route(posixpath.join("/", config.url_base, "api", "favs"))
def _api_favs():
    favs = backend.get_fav(db)
    fav_files = []
    for fav in favs:
        post_id = fav[0]
        fav_time = fav[1]  # Wed Jun  4 19:01:32 2025
        # to timestamp
        fav_time = time.mktime(time.strptime(fav_time, "%a %b %d %H:%M:%S %Y"))
        post = backend.Post(post_id, None, None)
        if not post.load_from_db(db):
            logger.log(f"Post [{post_id}] not found.")
            continue
        for row in db.query_rows(selected_db="media", key="post_id", value=post_id):
            media_id = row[0]
            media = backend.Media(media_id, post_id, None, post.type, post.time)
            media.load_from_db(db)
            fav_files.append(
                (
                    os.path.join(
                        config.fs_bases[post.type], post.user_name, media.file_name
                    ),
                    fav_time,
                )
            )
    return jsonify(fav_files)


@app.route(posixpath.join("/", config.url_base, "logs"))
def _logs():
    log_lines = logger.get_recent_logs(200)
    return render_template("logs.html", log_lines=log_lines, url_base=config.url_base)


@app.route(posixpath.join("/", config.url_base, "cache_proxy", "<path:subpath>"))
def cache_proxy(subpath):
    logger.log(f"Proxying request for: {subpath}", type="attention")
    if not "furaffinity.net" in subpath:
        logger.log(
            f"Access to {subpath} denied, be careful, someone is trying to access unauthorized domain, which means someone might be attempting a security breach.",
            type="error",
        )
        return "Not allowed.", 400
    subpath.lstrip("/")
    subpath = "https://" + subpath
    filename = subpath.split("/")[-1]
    cache_path = os.path.join("tmp/.cached", filename)
    if os.path.exists(cache_path):
        logger.log(f"Serving from cache: {cache_path}", verbose=1)
        return set_cache_header(send_file(cache_path))
    else:
        os.makedirs("tmp/.cached", exist_ok=True)
        logger.log(f"Fetching from remote: {subpath}")
        r = requests.get(subpath, headers=utils.headers)
        with open(cache_path, "wb") as f:
            f.write(r.content)
        logger.log(f"Cached to: {cache_path}")
        return set_cache_header(send_file(cache_path))


def build_cache_all_posts_id_thread(db):
    while True:
        try:
            if utils.has_new_download:
                utils.busy_flag = True
                logger.log("Building cache...")
                backend.build_cache(db)
                logger.log("Cache built.")
                utils.has_new_download = False
                utils.busy_flag = False
            else:
                logger.log("No new download, skipping cache build.")
        except Exception as e:
            logger.log(traceback.format_exc(), type="error")
            utils.busy_flag = False
        # Sleep for a while to avoid busy looping
        time.sleep(30 * 60)


def init(db, skip_scan):
    backend.scan_for_users("x", db)
    backend.scan_for_users("bsky", db)
    backend.scan_for_users("reddit", db)
    backend.scan_for_users("fa", db)
    if not skip_scan:
        backend.scan_for_posts("x", db)
        backend.scan_for_media("x", db)
        backend.scan_for_posts("bsky", db)
        backend.scan_for_media("bsky", db)
        backend.scan_for_posts("reddit", db)
        backend.scan_for_media("reddit", db)
        backend.scan_for_posts("fa", db)
        backend.scan_for_media("fa", db)
    db.commit()
    logger.log("Scan finished.")

    Thread(target=build_cache_all_posts_id_thread, args=(db,), daemon=True).start()
    logger.log("Cache building thread started.")

    if args.update_daemon:
        logger.log("Starting update daemon...")
        Thread(target=utils.update_daemon, daemon=True).start()


def shutdown_cleanup():
    utils.global_running_flag = False
    db.conn.close()


def signal_handler(signal, frame):
    shutdown_cleanup()
    sys.exit(0)


def wsgi_app(skip_scan=False):
    init(db, skip_scan)
    return app


db = backend.Database("data.db", "fav.db")
db.prepare_db()
utils.global_running_flag = True
worker = utils.DownloadWorker(db)
worker.setDaemon(True)
worker.start()
logger.log("Download worker started.")
logger.log("Ready.")

if __name__ == "__main__":
    init(db, args.skip_scan)
    logger.log(f"app is ready at: http://{config.host}:{config.port}/{config.url_base}")
    app.run(host=config.host, port=config.port, debug=args.debug)
    shutdown_cleanup()
else:
    signal.signal(signal.SIGTERM, signal_handler)  # Handle SIGTERM
