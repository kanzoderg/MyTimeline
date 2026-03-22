import sqlite3
import os, json, re, time, sys
from datetime import datetime, timezone
import natsort, random
import threading
import traceback

import config

if not config.config_read:
    print("Unit test: config not read, reading now...")
    config.read_config()

import utils, logger

debug_mode = False

from pympler import asizeof

db = None


def set_db(database):
    global db
    db = database


class Database:
    def __init__(self, db_file, fav_db_file):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.fav_conn = sqlite3.connect(fav_db_file, check_same_thread=False)
        self.cached_query_words = dict()
        self.query_search_results_counter = dict()
        self.last_text_query_time = -1
        self.db_lock = threading.Lock()

    def prepare_db(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY,
                user_name TEXT,
                udid TEXT,
                nick TEXT,
                avatar TEXT,
                banner TEXT,
                description TEXT,
                type TEXT,
                update_time INTEGER,
                flagged BOOLEAN DEFAULT 0
            )"""
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_user_name ON users(user_name)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_udid ON users(udid)")
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS posts (
                post_id TEXT PRIMARY KEY,
                text_content TEXT,
                uid TEXT,
                nick TEXT,
                time INTEGER,
                type TEXT,
                url TEXT,
                likes INTEGER,
                reposts INTEGER,
                comments INTEGER,
                embed TEXT,
                isreply BOOLEAN,
                reply_to TEXT,
                real_user TEXT,
                alt TEXT
            )"""
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts(uid)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_time ON posts(time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_likes ON posts(likes)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_reply_to ON posts(reply_to)"
            )
            # post_id to numid mapping
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS idmap (
                    numid INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id TEXT,
                    UNIQUE(post_id)
                )
            """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idmap_post_id ON idmap(post_id)")
            # Create FTS5 virtual table for full-text search on posts
            # Using standalone table (not content-less) for simpler synchronization
            cursor.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
                    post_id,
                    uid,
                    nick,
                    real_user,
                    text_content,
                    alt
                )"""
            )
            # Create triggers to keep FTS table in sync with posts table
            # Uses idmap.numid as rowid for posts_fts to ensure uniqueness
            # This eliminates slow EXISTS checks - SQLite's rowid uniqueness handles deduplication
            cursor.execute(
                """CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
                    INSERT INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt)
                    SELECT (SELECT numid FROM idmap WHERE post_id = new.post_id), new.post_id, new.uid, new.nick, COALESCE(new.real_user, ''), new.text_content, COALESCE(new.alt, '');
                END"""
            )
            cursor.execute(
                """CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
                    DELETE FROM posts_fts WHERE rowid = (SELECT numid FROM idmap WHERE post_id = old.post_id);
                END"""
            )
            cursor.execute(
                """CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
                    INSERT OR REPLACE INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt)
                    SELECT (SELECT numid FROM idmap WHERE post_id = new.post_id), new.post_id, new.uid, new.nick, COALESCE(new.real_user, ''), new.text_content, COALESCE(new.alt, '');
                END"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS media (
                media_id TEXT PRIMARY KEY,
                post_id TEXT,
                file_name TEXT,
                uid TEXT,
                type TEXT,
                ext INTEGER
            )"""
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_post_id ON media(post_id)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_uid ON media(uid)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_ext ON media(ext)")
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS user_group (
                uid TEXT,
                group_name TEXT,
                timestamp INTEGER,
                UNIQUE(uid, group_name)
                )"""
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_group_uid ON user_group(uid)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_group_group_name ON user_group(group_name)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_group_timestamp ON user_group(timestamp)"
            )

            cursor.execute(
                """CREATE TABLE IF NOT EXISTS external_link_lookup (
                url TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                thumbnail TEXT
                )"""
            )

            fav_cursor = self.fav_conn.cursor()
            fav_cursor.execute(
                """CREATE TABLE IF NOT EXISTS fav (
                post_id TEXT PRIMARY KEY,
                fav_time INTEGER
            )"""
            )
            fav_cursor.close()
            self.fav_conn.commit()
            cursor.close()
            self.conn.commit()
        except Exception as e:
            logger.log(f"Error preparing database: {e}", type="error")
            traceback.print_exc()
            logger.log(
                f"This error may be caused by an old incompatible database schema, try deleting the database files (data.db and data_fav.db) to fix this.",
                type="error",
            )
            sys.exit(1)

    def insert_or_update_user(
        self,
        uid,
        user_name,
        udid,
        nick,
        avatar,
        banner,
        description,
        type,
        update_time=None,
        flagged=0,
    ):
        if not update_time:
            update_time = time.time()
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    user_name,
                    udid,
                    nick,
                    avatar,
                    banner,
                    description,
                    type,
                    update_time,
                    flagged,
                ),
            )
            cursor.close()

    def insert_or_update_post(
        self,
        post_id,
        text_content,
        uid,
        nick,
        time,
        type,
        url,
        likes,
        reposts,
        comments,
        embed,
        isreply,
        reply_to="",
        real_user="",
        alt="",
    ):
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO
                posts(post_id, text_content, uid, nick, time, type, url, likes, reposts, comments, embed, isreply, reply_to, real_user, alt)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    post_id,
                    text_content,
                    uid,
                    nick,
                    time,
                    type,
                    url,
                    likes,
                    reposts,
                    comments,
                    embed,
                    isreply,
                    reply_to,
                    real_user,
                    alt,
                ),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO idmap(post_id) VALUES (?)", (post_id,)
            )
            cursor.close()

    def insert_or_update_media(self, media_id, post_id, file_name, uid, type, ext=0):
        with self.db_lock:
            if ext == 0:
                ext = utils.media_type_from_extension(file_name)
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO media VALUES (?,?,?,?,?,?)",
                (media_id, post_id, file_name, uid, type, ext),
            )
            cursor.close()

    def insert_or_update_url(self, url, title, description, thumbnail):
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO
                external_link_lookup
                VALUES (?,?,?,?)""",
                (url, title, description, thumbnail),
            )
            cursor.close()

    def query_rows(
        self,
        selected_table,
        key,
        value,
        ignore_cache=False,
        sort_key=None,
        reverse=True,
    ):
        if key:
            res = self.raw_query(
                (f"SELECT * FROM {selected_table} WHERE {key} = ?", (value,)),
                selected_db="fav" if selected_table == "fav" else "main",
                sort_key=sort_key,
                sort_reverse=reverse,
                ignore_cache=ignore_cache,
            )
        else:
            res = self.raw_query(
                f"SELECT * FROM {selected_table}",
                selected_db="fav" if selected_table == "fav" else "main",
                sort_key=sort_key,
                sort_reverse=reverse,
                ignore_cache=ignore_cache,
            )
        return res

    def raw_query(
        self,
        sql,
        selected_db="main",
        ignore_cache=False,
        sort_key=None,
        sort_reverse=False,
    ):
        global query_cache
        if len(query_cache) > query_cache_size_limit:
            logger.log("Clear query cache.")
            self.clear_cache()
        if (sql, sort_key) in query_cache and not ignore_cache:
            logger.log("Use cached raw query for", (sql, sort_key), verbose=3)
            return query_cache[(sql, sort_key)]
        else:
            logger.log("Executing raw query for", (sql, sort_key), verbose=2)
            with self.db_lock:
                if selected_db == "fav":
                    cursor = self.fav_conn.cursor()
                else:
                    cursor = self.conn.cursor()
                if type(sql) == str:
                    sql = sql.strip()
                    cursor.execute(sql)
                elif type(sql) in (list, tuple):
                    cursor.execute(*sql)
                else:
                    raise ValueError("sql must be a string or a tuple/list")
                res = cursor.fetchall()
                cursor.close()
            res = natsort.natsorted(res, key=sort_key) if sort_key else res
            if sort_reverse:
                res = list(reversed(res))
            if not ignore_cache:
                query_cache[(sql, sort_key)] = res
                logger.log("Cached raw query for", (sql, sort_key), verbose=2)
            else:
                logger.log("Not caching raw query for", (sql, sort_key), verbose=2)
            return res

    def get_post_count(self):
        res = self.raw_query("SELECT COUNT(*) FROM posts")
        return res[0][0] if res else 0

    def get_media_count(self):
        res = self.raw_query("SELECT COUNT(*) FROM media")
        return res[0][0] if res else 0

    def get_video_count(self):
        res = self.raw_query("""SELECT COUNT(*) FROM media WHERE ext = 1""")
        return res[0][0] if res else 0

    @utils.time_it
    def get_a_video(self, rowid):
        sql = f"""SELECT media_id FROM media 
            WHERE ext = 1 
            AND rowid >= {rowid}
            LIMIT 1"""
        res = self.raw_query(sql)
        return res

    @utils.time_it
    def query_post_by_text(
        self,
        text_content,
        offset,
        limit,
        sort_type="new",
        sort_reverse=False,
        include="post",
    ):
        text_content_original = text_content
        text_content = text_content.strip()

        # Check for "mode:full" to use LIKE-based search
        use_full_mode = "mode:full" in text_content.lower()

        words = tuple(
            set(
                [i.lstrip("u/").strip() for i in text_content.split() if i and i != " "]
            )
        )
        if (words, offset, limit, sort_type) in self.cached_query_words:
            if (
                abs(
                    self.cached_query_words[(words, offset, limit, sort_type)][0]
                    - time.time()
                )
                > 1200
            ):
                logger.log("Clear outdated query cache.", verbose=1)
                self.cached_query_words = dict()
            else:
                logger.log("Use cached query for", words, verbose=1)
                return (
                    self.cached_query_words[(words, offset, limit, sort_type)][1],
                    self.cached_query_words[(words, offset, limit, sort_type)][2],
                )
        logger.log(
            "Querying posts by text:",
            (words, sort_type, "full_mode" if use_full_mode else "fts_mode"),
            verbose=2,
        )

        if sort_type == "new":
            order_by = "p.time"
        elif sort_type == "top":
            order_by = "p.likes"
        elif sort_type == "random":
            order_by = "RANDOM()"
        else:
            order_by = "p.time"

        if use_full_mode:
            # Use LIKE-based search across uid, nick, text_content, alt
            # Build search pattern for each word
            like_patterns = [
                f"%{word}%"
                for word in words
                if word and not (word in utils.search_term_excludes)
            ]

            # Build LIKE conditions - check each field separately for proper Unicode handling
            # This ensures Chinese characters and other multi-byte Unicode are matched correctly
            fields = ["uid", "nick", "real_user", "text_content", "alt"]
            conditions = []
            params = []

            for pattern in like_patterns:
                # Each field is checked separately with OR, then all words are ANDed
                field_conditions = " OR ".join(
                    [f"p.{field} LIKE ? COLLATE NOCASE" for field in fields]
                )
                conditions.append(f"({field_conditions})")
                # Add the pattern for each field
                params.extend([pattern] * len(fields))
            params = tuple(params)

            if include == "video":
                conditions.append("m.ext=1")
            where_clause = " AND ".join(conditions)
            sql_query = f"""
                SELECT p.* FROM posts p
                WHERE {where_clause}
                ORDER BY {order_by} DESC
                LIMIT {limit} OFFSET {offset}
            """
            logger.log(sql_query, params)
            res = self.raw_query(
                (sql_query, params),
                sort_reverse=sort_reverse,
            )

            # Count total results
            count_sql = f"SELECT COUNT(*) FROM posts p WHERE {where_clause}"
            count_res = self.raw_query(
                (count_sql, params),
            )
            count = count_res[0][0] if count_res else 0
        else:
            # Build FTS5 query string
            fts_query = " AND ".join(
                [
                    f'"{word}"'
                    for word in words
                    if word and not (word in utils.search_term_excludes)
                ]
            )
            conditions = ["posts_fts MATCH ?"]
            if include == "video":
                conditions.append("m.ext=1")
            where_clause = " AND ".join(conditions)
            # Use FTS5 table for full-text search, join with posts table for full data
            sql_query = f"""
                SELECT p.* FROM posts p
                INNER JOIN posts_fts fts ON p.post_id = fts.post_id
                WHERE {where_clause}
                ORDER BY {order_by} DESC
                LIMIT {limit} OFFSET {offset}
            """
            params = (fts_query,)
            res = self.raw_query(
                (sql_query, params),
                sort_reverse=sort_reverse,
            )
            logger.log(sql_query, params)

            # Count total results for pagination
            count_res = self.raw_query(
                (
                    f"""SELECT COUNT(*) FROM posts p 
                    INNER JOIN posts_fts fts ON p.post_id = fts.post_id 
                    WHERE {where_clause}""",
                    params,
                )
            )
            count = count_res[0][0] if count_res else 0
            logger.log(f"Counted {count} total results for text query.", verbose=1)

        self.query_search_results_counter[(text_content_original, "p")] = count

        self.cached_query_words[(words, offset, limit, sort_type)] = (
            time.time(),
            res,
            count,
        )
        return res, count

    @utils.time_it
    def query_video_by_uid(self, uid, offset, limit):
        global query_cache_media
        uid = uid.strip().lower()
        if (uid, offset, limit) in query_cache_media:
            if abs(query_cache_media[(uid, offset, limit)][0] - time.time()) <= 1200:
                logger.log("Use cached uid video query.", verbose=1)
                return (
                    query_cache_media[(uid, offset, limit)][1],
                    query_cache_media[(uid, offset, limit)][2],
                )
        if not (uid, "v") in self.query_search_results_counter:
            count_res = self.raw_query(
                ("SELECT COUNT(*) FROM media WHERE uid = ? AND ext=1", (uid,))
            )
            count = count_res[0][0] if count_res else 0
            self.query_search_results_counter[(uid, "v")] = count
        else:
            count = self.query_search_results_counter[(uid, "v")]
        logger.log("Querying videos by uid:", uid, verbose=1)
        offset = offset % count if count > 0 else 0
        sql_query = f"""SELECT media_id FROM media 
            WHERE uid = ? AND ext=1
            ORDER BY media_id DESC
            LIMIT {limit} OFFSET {offset}"""
        res = self.raw_query((sql_query, (uid,)))
        if res:
            res = [i[0] for i in res]
            query_cache_media[(uid, offset, limit)] = (time.time(), res, count)
        else:
            query_cache_media[(uid, offset, limit)] = (time.time(), [], 0)
        return (
            query_cache_media[(uid, offset, limit)][1],
            query_cache_media[(uid, offset, limit)][2],
        )

    def query_fav_videos(self):
        global query_cache_media

        favs = get_fav()
        favs = [fav[0] for fav in favs]
        if not favs:
            return [], 0
        if "fav" in query_cache_media:
            if abs(query_cache_media["fav"][0] - time.time()) <= 1200:
                logger.log("Use cached fav video query.", verbose=1)
                return query_cache_media["fav"][1], query_cache_media["fav"][2]
        logger.log("Querying fav videos.", verbose=1)
        res = []
        for fav in favs:
            media_ids = self.raw_query(
                (
                    "SELECT media_id, ext FROM media WHERE post_id = ?",
                    (fav,),
                )
            )
            media_ids = [
                media_id for media_id in media_ids if media_id[1] == utils.VIDEO
            ]
            res.extend(media_ids)
        res = res[::-1]  # reverse to have newest first
        if res:
            res = [i[0] for i in res]
            query_cache_media["fav"] = (time.time(), res, len(res))
        else:
            query_cache_media["fav"] = (time.time(), [], 0)
        return query_cache_media["fav"][1], query_cache_media["fav"][2]

    @utils.time_it
    def get_new(self, start_idx, limit):
        sql = f"""SELECT post_id FROM posts ORDER BY time DESC LIMIT {limit} OFFSET {start_idx}"""
        res = self.raw_query(sql)
        return res

    @utils.time_it
    def get_top(self, start_idx, limit):
        sql = f"""SELECT post_id FROM posts ORDER BY likes DESC LIMIT {limit} OFFSET {start_idx}"""
        res = self.raw_query(sql)
        return res

    @utils.time_it
    def get_random(self, limit):
        post_count = self.get_post_count()
        limit = min(limit, post_count)
        random_indexes = [random.randint(0, post_count - 1) for _ in range(limit)]
        res = []
        for idx in random_indexes:
            sql = f"""SELECT post_id FROM posts LIMIT 1 OFFSET {idx}"""
            row = self.raw_query(sql)
            if row:
                res.append(row[0])
        return res

    def commit(self):
        self.conn.commit()
        self.fav_conn.commit()

    def clear_cache(self):
        global query_cache, query_cache_media
        query_cache = dict()
        query_cache_media = dict()
        self.cached_query_words = dict()
        self.query_search_results_counter = dict()


class ExternalLink:
    def __init__(self, url):
        self.url = url
        self.clean_url = url.split("?")[0].split("#")[0]
        self.title = "Link Title"
        self.description = "External Link"
        self.thumbnail = ""

    def probe(self):
        rows = db.query_rows("external_link_lookup", "url", self.url, ignore_cache=True)
        if len(rows) > 0:
            self.title, self.description, self.thumbnail = rows[0][1:]
        else:
            self.title, self.description, self.thumbnail = utils.probe_url(self.url)
            db.insert_or_update_url(
                self.url, self.title, self.description, self.thumbnail
            )
            db.commit()


class Post:
    def __init__(self, post_id, user_name, type):
        self.post_id = post_id
        self.user_name = user_name
        self.type = type
        self.uid = f"{user_name}@{type}" if user_name and type else None
        self.likes = 0
        self.reposts = 0
        self.comments = 0
        self.nick = ""
        self.fav = False
        self.embed = ""
        self.isreply = False
        self.is_externalreply = False
        self.reply_to = ""
        self.text_content = ""
        self.url = ""
        self.time = 0
        self.is_external = False
        if type == "reddit":
            self.real_user = "[deleted]"
        else:
            self.real_user = ""
        self.alt = ""
        self.alts = []
        self.loaded = False
        self.json_error = False
        self.external_link = None

    def load_from_db(self, ignore_cache=False):
        rows = db.query_rows(
            "posts", "post_id", self.post_id, ignore_cache=ignore_cache
        )
        if len(rows) == 0:
            self.is_external = True
            self.concat_url()
            self.loaded = False
            return False
        self.is_external = False
        row = rows[0]
        self.uid = row[2]
        # Extract user_name and type from uid if not already set
        if self.uid and "@" in self.uid:
            parts = self.uid.rsplit("@", 1)
            self.user_name = parts[0]
            self.type = parts[1]
        self.nick = row[3]
        self.time = row[4]
        self.type = row[5]
        self.url = row[6]
        self.likes = row[7]
        self.reposts = row[8]
        self.comments = row[9]
        self.embed = row[10]
        self.isreply = row[11]
        self.reply_to = row[12]
        self.real_user = row[13]
        self.alt = row[14]
        if self.alt:
            self.alts = self.alt.split("<sep>")
        self.text_content, link = utils.embed_hyperlink(self.type, row[1])
        if link:
            if utils.check_allowed_to_embed(link):
                self.embed = self.embed or link
            elif utils.check_allowed_to_probe(link):
                self.external_link = ExternalLink(link)
                self.external_link.probe()
            else:
                self.embed = ""
        # check if post is in fav, ignore cache to always get latest fav status
        rows = db.query_rows("fav", "post_id", self.post_id, ignore_cache=True)
        if len(rows) > 0:
            self.fav = True
        self.loaded = True
        return True

    def save_to_db(self):
        if self.loaded and self.json_error:
            logger.log(
                "Failed to load json for",
                self.uid,
                self.url,
                ", but since its successfully loaded before, dummy info won't be wrote.",
                type="warning",
            )
            return
        db.insert_or_update_post(
            self.post_id,
            self.text_content,
            self.uid,
            self.nick,
            self.time,
            self.type,
            self.url,
            self.likes,
            self.reposts,
            self.comments,
            self.embed,
            self.isreply,
            self.reply_to,
            self.real_user,
            self.alt,
        )

    def load_from_json(self, json):
        try:
            if self.type == "x":
                self.post_id = str(json["tweet_id"])
                self.text_content = json["content"]
                if not self.user_name:
                    self.user_name = json["author"]["name"].lower()
                self.nick = json["author"]["nick"]
                self.time = json["date"]  # eg. 2026-02-07 04:09:32
                dt = datetime.strptime(self.time, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                self.time = int(dt.timestamp())
                self.url = f"https://x.com/{self.user_name}/status/{self.post_id}"
                self.likes = json["favorite_count"]
                self.reposts = json["retweet_count"]
                self.comments = json["reply_count"]

                self.isreply = "reply_to" in json
                reply_id = json.get("reply_id", "")
                reply_to_user = json.get("reply_to", "")
                if reply_id and reply_to_user:
                    self.reply_to = f"{reply_id}@{reply_to_user.lower()}"
            elif self.type == "bsky":
                self.post_id = str(json["post_id"])
                self.text_content = json["text"]
                if "facets" in json:
                    self.text_content = bsky_link_fix(self.text_content, json["facets"])
                if not self.user_name:
                    self.user_name = json["author"]["handle"].lower()
                self.nick = json["author"]["displayName"]
                self.time = json["date"]  # eg. 2026-02-07 04:09:32
                dt = datetime.strptime(self.time, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                self.time = int(dt.timestamp())
                self.url = (
                    f"https://bsky.app/profile/{self.user_name}/post/{self.post_id}"
                )
                self.likes = json["likeCount"]
                self.reposts = json["repostCount"]
                self.comments = json["replyCount"]
                if "embed" in json and "record" in json["embed"]:
                    try:
                        self.embed = json["embed"]["record"]["uri"]
                    except:
                        self.embed = json["embed"]["record"]["record"]["uri"]
                self.isreply = "reply" in json
                reply_parent = json.get("reply", {}).get("parent", {})
                if reply_parent:
                    # print("*"*1000,"reply parent:", reply_parent)
                    reply_match = re.match(
                        r"at://([^/]+)/app.bsky.feed.post/([^/]+)$",
                        reply_parent.get("uri", ""),
                    )
                    if reply_match:
                        self.reply_to = f"{reply_match.group(2)}@{reply_match.group(1)}"
                self.alt = ""
                self.alts = []
                for image in json.get("embed", {}).get("images", []) or json.get(
                    "embed", {}
                ).get("media", {}).get("images", []):
                    image_alt = image.get("alt", "")
                    if image_alt:
                        self.alts.append(image_alt)
                if self.alts:
                    self.alt = "<sep>".join(self.alts)
            elif self.type == "reddit":
                self.post_id = json["id"]
                self.text_content = (
                    f"<span class='rdt_title'>{json['title']}</span>{json['selftext']}"
                )
                self.user_name = json["subreddit"].lower()
                self.nick = self.user_name
                self.time = int(json["created_utc"])
                self.url = (
                    f"https://reddit.com/r/{self.user_name}/comments/{self.post_id}"
                )
                self.likes = json["score"]
                self.reposts = 0
                self.comments = json["num_comments"]
                self.isreply = False
                self.real_user = json["author"]
            elif self.type == "fa":
                self.post_id = str(json["id"])
                self.text_content = f"<span class='rdt_title'>{json['title']}</span>{json['description']}"
                self.user_name = json["user"].lower()
                self.nick = json.get("artist", self.user_name)
                self.time = json["date"]  # eg. 2022-03-10 10:09:11
                self.time = int(
                    time.mktime(time.strptime(self.time, "%Y-%m-%d %H:%M:%S"))
                )
                if (
                    json.get("subcategory") == "journals"
                    or json.get("category") == "journals"
                ):
                    self.url = f"https://www.furaffinity.net/journal/{self.post_id}/"
                else:
                    self.url = f"https://www.furaffinity.net/view/{self.post_id}/"
                self.likes = json.get("favorites", 0)
                self.reposts = 0
                self.comments = json.get("comments", 0)
                self.isreply = False
                self.real_user = self.user_name
        except Exception as e:
            logger.log(f"Error loading post from json: {e}", type="error")
            logger.log(json, type="error")
            self.json_error = True
        # Set uid after user_name and type are determined
        self.uid = f"{self.user_name}@{self.type}"
        self.save_to_db()

    def init_medias(self):
        self.medias = []
        for row in db.query_rows(
            selected_table="media", key="post_id", value=self.post_id
        ):
            media_id = row[0]
            media = Media(media_id, self.post_id, self.user_name, self.type)
            media.load_from_db()
            self.medias.append(media)
        self.medias = natsort.natsorted(self.medias, key=lambda x: x.media_id)

    def init_embed(self):
        if self.embed:
            if self.embed.startswith("at://"):
                post_id = self.embed.split("/")[-1]
                user_name = self.embed.split("/")[-3]
                type_ = "bsky"
            elif (
                "furaffinity.net/view/" in self.embed
                or "furaffinity.net/journal/" in self.embed
            ):
                post_id = self.embed.split("?")[0].strip("/").split("/")[-1]
                user_name = ""
                type_ = "fa"
            else:
                self.embed = ""
                return
            self.embed_obj = Post(post_id, user_name, type_)
            self.embed_obj.is_external = not self.embed_obj.load_from_db()
            if not self.embed_obj.is_external:
                self.embed_obj.init_medias()
            self.embed_obj.concat_url()

    def concat_url(self):
        if self.type == "x":
            self.url = f"https://x.com/{self.user_name}/status/{self.post_id}"
        elif self.type == "bsky":
            self.url = f"https://bsky.app/profile/{self.user_name}/post/{self.post_id}"
        elif self.type == "reddit":
            self.url = f"https://reddit.com/r/{self.user_name}/comments/{self.post_id}"
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/view/{self.post_id}/"

    def get_time_str(self):
        now = time.time()
        if now - self.time < 60:
            return "Just now"
        elif now - self.time < 3600:
            return f"{int((now - self.time) / 60)} minutes ago"
        elif now - self.time < 86400:
            return f"{int((now - self.time) / 3600)} hours ago"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.time))


class User:
    def __init__(self, user_name, type=""):
        self.placeholder = False
        if not user_name:
            self.placeholder = True
        self.user_name = user_name.lower() if user_name else ""
        self.type = type
        self.uid = f"{self.user_name}@{self.type}" if type else None
        self.nick = ""
        self.udid = self.user_name
        self.update_time = 0
        self.flagged = 0
        self.avatar = ""
        self.banner = ""
        self.description = ""
        self.url = ""

    def load_from_db(self, ignore_cache=False):
        self.concat_url()
        if self.placeholder:
            return False
        # Query by uid if we have type, otherwise query by user_name
        if self.type:
            rows = db.query_rows("users", "uid", self.uid, ignore_cache)
        else:
            rows = db.query_rows("users", "user_name", self.user_name, ignore_cache)
        if rows:
            try:
                row = rows[0]
                self.uid = row[0]
                self.user_name = row[1]
                self.udid = row[2]
                self.nick = row[3]
                self.avatar = row[4]
                self.banner = row[5]
                self.description, _ = utils.embed_hyperlink(self.type, row[6])
                self.type = row[7]
                self.update_time = row[8]
                self.flagged = row[9]
                if not self.nick:
                    self.nick = self.user_name
                return True
            except Exception as e:
                logger.log(
                    f"Error loading user {self.user_name} from database: {e}",
                    type="error",
                )
                logger.log(rows, type="error")
                return False
        return False

    def load_from_inline(
        self,
        uid,
        user_name,
        nick,
        udid,
        avatar,
        banner,
        description,
        type,
        update_time,
        flagged,
    ):
        self.uid = uid
        self.user_name = user_name
        self.nick = nick
        self.udid = udid
        self.avatar = avatar
        self.banner = banner
        self.description = description
        self.type = type
        self.update_time = update_time
        self.flagged = flagged
        self.concat_url()
        if not self.nick:
            self.nick = self.user_name

    def save_to_db(self):
        if self.placeholder:
            return
        db.insert_or_update_user(
            self.uid,
            self.user_name,
            self.udid,
            self.nick,
            self.avatar,
            self.banner,
            self.description,
            self.type,
            self.update_time,
            self.flagged,
        )

    def load_from_json(self, json, use_fs_modified_time=False):
        if self.placeholder:
            return
        if self.type == "x":
            self.nick = json["author"]["nick"]
            self.udid = self.user_name
            self.avatar = json["author"]["profile_image"]
            self.banner = ""
            self.description = ""
            try:
                self.banner = json["author"]["profile_banner"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has no banner.\ndownload again with lasest gallery-dl version to fix this.",
                    type="warning",
                )
            try:
                self.description = json["author"]["description"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has nodescription.\ndownload again with lasest gallery-dl version to fix this.",
                    type="warning",
                )
        elif self.type == "bsky":
            self.nick = json["author"]["displayName"]
            self.udid = json["author"]["did"]
            try:
                self.avatar = json["author"]["avatar"]
            except:
                self.avatar = ""
                logger.log(
                    f"warning: user {self.user_name} has no avatar.\ndownload again with lasest gallery-dl version to fix this.",
                    type="error",
                )
            self.banner = ""
            self.description = ""
            try:
                self.banner = json["user"]["banner"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has no banner.", type="error"
                )
            try:
                self.description = json["user"]["description"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has no description.", type="error"
                )
        elif self.type == "reddit":
            self.nick = self.user_name
            self.udid = self.user_name
            self.avatar = ""
            self.banner = ""
            self.description = f"Reddit subreddit {self.user_name}.\n"
            try:
                about_json = utils.get_reddit_about(self.user_name)
                self.description += about_json.get("public_description", "")
                self.banner = (
                    about_json.get("banner_background_image", "").split("?")[0]
                    or about_json.get("banner_img", "").split("?")[0]
                )
                self.avatar = (
                    about_json.get("community_icon", "").split("?")[0]
                    or about_json.get("icon_img", "").split("?")[0]
                )
            except Exception as e:
                logger.log(
                    f"warning: could not fetch reddit about for {self.user_name}: {e}",
                    type="error",
                )
        elif self.type == "fa":
            self.nick = json["display_name"]
            self.udid = self.user_name
            self.avatar = json["avatar_url"]
            self.banner = json["banner_url"]
            self.description = json["description"]
        if use_fs_modified_time:
            self.update_time = os.path.getmtime(
                os.path.join(config.fs_bases[self.type], self.user_name)
            )
        else:
            self.update_time = time.time()
        self.concat_url()
        self.save_to_db()

    def concat_url(self):
        if self.type == "x":
            self.url = f"https://x.com/{self.user_name}"
        elif self.type == "bsky":
            self.url = f"https://bsky.app/profile/{self.user_name}"
        elif self.type == "reddit":
            if self.user_name == "reddit_users":
                self.url = "#"
            else:
                self.url = f"https://reddit.com/r/{self.user_name}"
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/user/{self.user_name}"

    def get_update_time_str(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.update_time))


class Media:
    def __init__(self, media_id, post_id, user_name, type):
        self.media_id = media_id
        self.post_id = post_id
        self.user_name = user_name
        self.type = type
        self.uid = f"{user_name}@{type}" if user_name and type else None

    def save_to_db(self):
        db.insert_or_update_media(
            self.media_id, self.post_id, self.file_name, self.uid, self.type
        )

    def load_from_db(self):
        rows = db.query_rows("media", "media_id", self.media_id)
        if len(rows) == 0:
            return False
        row = rows[0]
        try:
            self.post_id = row[1]
            self.file_name = row[2]
            if not self.file_name:
                return False
            self.uid = row[3]
            # Extract user_name and type from uid if not already set
            if self.uid and "@" in self.uid:
                parts = self.uid.rsplit("@", 1)
                if not self.user_name:
                    self.user_name = parts[0]
                if not self.type:
                    self.type = parts[1]
            self.type = row[4]
            self.isvideo = (
                utils.media_type_from_extension(self.file_name) == utils.VIDEO
            )
            self.isaudio = (
                utils.media_type_from_extension(self.file_name) == utils.AUDIO
            )
            self.isimage = (
                utils.media_type_from_extension(self.file_name) == utils.IMAGE
            )
            self.isflash = (
                utils.media_type_from_extension(self.file_name) == utils.FLASH
            )
            self.isattachment = (
                utils.media_type_from_extension(self.file_name) == utils.TEXT
            )
        except Exception as e:
            logger.log("Error:", e, type="error")
            logger.log("Rows:", rows, type="error")
            return False
        return True


def bsky_link_fix(text, facets):
    try:
        for facet in facets:
            for feature in facet["features"]:
                if feature["$type"] != "app.bsky.richtext.facet#link":
                    continue
                uri = feature["uri"].replace("https://", "").replace("http://", "")
                length = abs(facet["index"]["byteEnd"] - facet["index"]["byteStart"])
                if length < len(uri):
                    shortened_uri = uri[: length - 3] + "..."
                    text = text.replace(shortened_uri, uri)
                    # logger.log(
                    #     f"warning: bsky link {shortened_uri} is shortened, replaced with {uri}"
                    # )
    except Exception as e:
        logger.log(f"Error fixing bsky link: {e}", type="error")
    return text


# scan for content downloaded using gallery-dl
def scan_for_users(type, user_name=None):
    global all_users
    if user_name == "ignore":
        return
    fs_base = config.fs_bases[type]
    # assume that the user name is the same as the directory name
    if not user_name:
        user_names = os.listdir(fs_base)
    else:
        user_names = [user_name]
    for user_name in user_names:
        try:
            if not os.path.exists(os.path.join(fs_base, user_name)):
                logger.log(user_name, "does not exists!")
                continue
            elif not os.path.isdir(os.path.join(fs_base, user_name)):
                logger.log(user_name, "is not a dir!")
                continue
            elif user_name.startswith("."):
                continue
            logger.log(f"scanning for user {user_name}")
            user = User(user_name, type)
            if not user.load_from_db(True) or len(user_names) == 1:
                # user not found in database, create a new entry
                # select the first json file
                json_files = []
                if type == "fa" and os.path.exists(
                    os.path.join(fs_base, user_name, "user.json")
                ):
                    json_files = ["user.json"]
                elif (
                    type in ["x", "bsky", "reddit"] and not user_name == "reddit_users"
                ):
                    file_list = os.listdir(os.path.join(fs_base, user_name))
                    json_files = [f for f in file_list if f.endswith(".json")]
                    json_files = natsort.natsorted(json_files, reverse=True)
                if len(json_files) > 0:
                    logger.log(f"found user json file: {json_files[0]}")
                    with open(
                        os.path.join(fs_base, user_name, json_files[0]),
                        "r",
                        encoding="utf=8",
                    ) as f:
                        user_json = json.load(f)
                        if len(user_names) == 1:
                            user.load_from_json(user_json)
                        else:
                            user.load_from_json(user_json, True)
                else:
                    # no json file found, use dummy values
                    user.nick = user_name
                    user.avatar = ""
                    user.banner = ""
                    if user_name == "reddit_users":
                        user.description = (
                            "Includes user posts that not belonging to any subreddit."
                        )
                    else:
                        user.description = ""
                    user.update_time = time.time()
                    user.save_to_db()
        except Exception as e:
            logger.log(e, type="error")
            logger.log("Error loading user:", user_name, type="error")
        db.commit()
    db.clear_cache()
    all_users = get_users()


def scan_for_posts(type, user_name=None, force=False):
    if force:
        logger.log("Forced rescan, will read all json files.")
    if user_name == "ignore":
        return
    fs_base = config.fs_bases[type]
    if not user_name:
        user_names = os.listdir(fs_base)
    else:
        user_names = [user_name]
    for cnt, user_name in enumerate(user_names):
        logger.log(
            f"[{cnt+1}/{len(user_names)}] scanning for posts of user {user_name}"
        )
        filelist = os.listdir(os.path.join(fs_base, user_name))
        # check if is file
        filelist = [
            f for f in filelist if os.path.isfile(os.path.join(fs_base, user_name, f))
        ]
        exclude_files = ["thumbs.db", ".ds_store", "user.json", "about.json"]
        filelist = [f for f in filelist if f.lower() not in exclude_files]

        # read json files in the user directory
        json_files = [f for f in filelist if f.endswith(".json")]
        regex_map = {
            "x": {"file_patterns": [r"\d+.+json"], "id_pattern": r"(\d+)"},
            "bsky": {
                "file_patterns": [r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}.+\.json"],
                "id_pattern": r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_([^_]+).+",
            },
            "reddit": {
                "file_patterns": [r".+json"],
                "id_pattern": r"([a-zA-Z0-9]+)",
            },
            "fa": {
                "file_patterns": [r"\d+"],
                "id_pattern": r"(\d+)",
            },
        }
        post_files = []
        patterns = regex_map[type]
        for pat in patterns["file_patterns"]:
            post_files += [f for f in json_files if re.match(pat, f)]
        for post_file in post_files:
            post_id = re.match(patterns["id_pattern"], post_file).group(1)
            post = Post(post_id, user_name, type)
            if (not post.load_from_db()) or force:
                json_file = os.path.join(fs_base, user_name, post_file)
                logger.log("Reading json file:", json_file, verbose=3)
                with open(
                    json_file,
                    "r",
                    encoding="utf-8",
                ) as f:
                    try:
                        post_json = json.load(f)
                        post.load_from_json(post_json)
                    except Exception as e:
                        logger.log(e, type="error")
                        logger.log(
                            "Error loading:",
                            json_file,
                            type="error",
                        )
                        if debug_mode:
                            traceback.print_exc()
        db.commit()
        # scan for media
        logger.log(
            f"[{cnt+1}/{len(user_names)}] scanning for media of user {user_name}"
        )
        media_files = [f for f in filelist if utils.media_type_from_extension(f) > 0]
        for media_file in media_files:
            if type in ["x", "bsky", "reddit"]:
                media_id = media_file.split(".")[0]
                if type == "x":
                    id_pattern = r"(\d+)"
                elif type == "bsky":
                    id_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_([^_]+).+"
                elif type == "reddit":
                    id_pattern = r"([a-zA-Z0-9]+)"
                elif type == "fa":
                    id_pattern = r"(\d+)"
                try:
                    related_post_id = re.match(id_pattern, media_file).group(1)
                    # logger.log("matching post_id from filename:", related_post_id)
                except:
                    related_post_id = "0" + media_file
                    logger.log(
                        "warning: no post_id found in filename:",
                        media_file,
                        type="error",
                    )
                if related_post_id in ["redgifs", "tumblr", "imgur", "gfycat"]:
                    related_post_id = "-1" + user_name + "_" + related_post_id
            elif type == "fa":
                media_id = media_file
                if os.path.exists(
                    os.path.join(fs_base, user_name, media_file + ".json")
                ):
                    with open(
                        os.path.join(fs_base, user_name, media_file + ".json"),
                        "r",
                        encoding="utf=8",
                    ) as f:
                        try:
                            post_json = json.load(f)
                            related_post_id = str(post_json["id"])
                        except Exception as e:
                            logger.log(e)
                            related_post_id = "0" + media_file
                            logger.log(
                                "Error loading:",
                                os.path.join(fs_base, user_name, media_file + ".json"),
                                type="error",
                            )
                            if debug_mode:
                                raise e
                else:
                    related_post_id = "0" + media_file
                    logger.log(
                        "warning: no json file found for media:",
                        os.path.join(fs_base, user_name, media_file),
                    )
            else:
                continue
            # test if related post exists
            post = Post(related_post_id, user_name, type)
            if not post.load_from_db(True):
                logger.log(
                    f"warning: media {media_id} has no related post {related_post_id} in database"
                )
                # create a dummy post
                post.text_content = media_file
                post.user_name = user_name
                guessed_timestamp = re.search(r"\d{10}", media_id)
                if guessed_timestamp and time.gmtime() > time.gmtime(
                    int(guessed_timestamp.group(0))
                ):
                    post.time = int(guessed_timestamp.group(0))
                else:
                    post.time = int(
                        os.path.getmtime(
                            os.path.join(config.fs_bases[type], user_name, media_file)
                        )
                    )
                post.type = type
                if type == "reddit" and re.match(r"[a-zA-Z0-9]{6,8}_\d", media_id):
                    dummy_id = media_id.split("_")[0]
                    post.url = f"https://reddit.com/r/{user_name}/comments/{dummy_id}"
                else:
                    post.url = ""
                post.likes = 0
                post.reposts = 0
                post.comments = 0
                post.save_to_db()
            media = Media(media_id, related_post_id, user_name, type)
            media.file_name = media_file
            if not media.load_from_db():
                media.save_to_db()
        db.commit()
    db.clear_cache()


def get_users():
    rows = db.raw_query("SELECT * FROM users")
    users = []
    for row in rows:
        uid = row[0]
        user_name = row[1]
        # Extract type from uid
        type = uid.split("@")[1] if "@" in uid else row[7]
        user = User(user_name, type)
        user.load_from_inline(
            row[0],
            row[1],
            row[3],
            row[2],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
        )
        users.append(user)
    users.sort(key=lambda u: u.update_time, reverse=True)
    return users


def get_usernames_by_type(type):
    rows = db.raw_query(f"SELECT * FROM users WHERE type = '{type}'")
    usernames = []
    for row in rows:
        user_name = row[1]
        usernames.append(user_name)
    return usernames


def flag_user(user_name, type):
    global all_users
    uid = f"{user_name}@{type}"
    # logger.log(f"*********Flagging user {uid}")
    # logger.log(f"UPDATE users SET flagged = 1 WHERE uid = \"{uid}\"")
    db.raw_query(f'UPDATE users SET flagged = 1 WHERE uid = "{uid}"', "main", True)
    db.commit()
    db.clear_cache()
    all_users = get_users()


def unflag_user(user_name, type):
    global all_users
    uid = f"{user_name}@{type}"
    # logger.log(f"*********Unflagging user {uid}")
    # logger.log(f"UPDATE users SET flagged = 0 WHERE uid = \"{uid}\"")
    db.raw_query(f'UPDATE users SET flagged = 0 WHERE uid = "{uid}"', "main", True)
    db.commit()
    db.clear_cache()
    all_users = get_users()


all_users = []

query_cache_size_limit = 2000
query_cache = dict()
query_cache_media = dict()


@utils.time_it
def get_fav():
    return db.query_rows(selected_table="fav", key="", value="", ignore_cache=True)


def add_favorite(post_id):
    if not db.query_rows("posts", "post_id", post_id):
        return
    db.raw_query(
        f"INSERT OR REPLACE INTO fav VALUES ('{post_id}', '{time.ctime()}')",
        "fav",
        True,
    )
    db.commit()


def remove_favorite(post_id):
    db.raw_query(f"DELETE FROM fav WHERE post_id = '{post_id}'", "fav", True)
    db.commit()


def get_user_groups():
    rows = db.raw_query(
        "SELECT DISTINCT group_name FROM user_group ORDER BY timestamp DESC",
        ignore_cache=True,
    )
    groups = [row[0] for row in rows]
    return groups


def get_uids_in_group(group_name):
    rows = db.raw_query(
        f"SELECT uid FROM user_group WHERE group_name = '{group_name}'",
        ignore_cache=True,
    )
    return [row[0] for row in rows]


def add_user_to_group(uid, group_name):
    db.raw_query(
        f"INSERT OR REPLACE INTO user_group VALUES ('{uid}', '{group_name}', '{time.ctime()}')",
        "user_group",
        ignore_cache=True,
    )
    db.commit()


def remove_user_from_group(uid, group_name):
    db.raw_query(
        f"DELETE FROM user_group WHERE uid = '{uid}' AND group_name = '{group_name}'",
        "user_group",
        ignore_cache=True,
    )
    db.commit()


def rename_group(old_group_name, new_group_name):
    db.raw_query(
        f"UPDATE user_group SET group_name = '{new_group_name}' WHERE group_name = '{old_group_name}'",
        "user_group",
        ignore_cache=True,
    )
    db.commit()


def get_all_usernames(type):
    rows = db.raw_query(
        f"SELECT user_name FROM users WHERE type = '{type}'", ignore_cache=True
    )
    return [row[0] for row in rows]


if not os.path.exists(config.fs_bases["x"]):
    os.makedirs(config.fs_bases["x"])
if not os.path.exists(config.fs_bases["bsky"]):
    os.makedirs(config.fs_bases["bsky"])

if __name__ == "__main__":
    db = Database("test.db")
    db.prepare_db()
    set_db()
    scan_for_users("x")
    scan_for_posts("x")
    scan_for_media("x")
    db.conn.close()
