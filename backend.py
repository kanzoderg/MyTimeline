import sqlite3
import os, json, re, time, sys, gzip
from datetime import datetime, timezone
import natsort, random
from urllib.parse import unquote, quote
import threading
import traceback

import config

if not config.config_read:
    print("Unit test: config not read, reading now...")
    config.read_config()

import utils, logger

debug_mode = False

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
                flagged BOOLEAN DEFAULT 0,
                extra_data TEXT
            )"""
            )
            # ALTER TABLE users ADD extra_data TEXT
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_user_name ON users(user_name)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_udid ON users(udid)")

            # Posts table with indexes optimized for various query patterns
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
                alt TEXT,
                extra_data TEXT
            )"""
            )
            # ALTER TABLE posts ADD extra_data TEXT
            # Global Sorting
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_time ON posts(time DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_likes ON posts(likes DESC)"
            )

            # Thread/Reply Handling
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_reply_to ON posts(reply_to)"
            )

            # User Specific (Composite indexes cover single-column lookups)
            # Covers: WHERE uid = ? AND ORDER BY time
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_uid_time ON posts(uid, time DESC)"
            )
            # Covers: WHERE uid = ? AND ORDER BY likes (Added for sort_type="top" + uid)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_uid_likes ON posts(uid, likes DESC)"
            )

            # REMOVED: idx_posts_uid (Redundant due to composite indexes above)

            # --- MEDIA TABLE ---
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS media (  
                media_id TEXT PRIMARY KEY,  
                post_id TEXT,  
                file_name TEXT,  
                uid TEXT,  
                type TEXT,  
                ext INTEGER,  
                size INTEGER,  
                duration INTEGER,  
                extra_data TEXT
            )"""
            )
            # ALTER TABLE media ADD extra_data TEXT

            # Crucial for JOIN: media -> posts
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_post_id ON media(post_id)"
            )

            # Crucial for Filtering: WHERE ext = 1 (Covering index includes media_id)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_ext_post ON media (ext, post_id, media_id)"
            )

            # User Media Lookup (Optional, keep if you query media by uid directly)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_uid ON media(uid)")

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
        extra_data={},
    ):
        if not update_time:
            update_time = time.time()
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
                    json.dumps(extra_data),
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
        extra_data={},
    ):
        if not "@" in post_id:
            raise ValueError(
                f"post_id must contain type suffix, e.g. '12345@x' user: {uid}, post_id: {post_id}"
            )
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    json.dumps(extra_data),
                ),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO idmap(post_id) VALUES (?)", (post_id,)
            )
            cursor.close()

    def insert_or_update_media(
        self,
        media_id,
        post_id,
        file_name,
        uid,
        type,
        ext=0,
        size=0,
        duration=0,
        extra_data={},
    ):
        with self.db_lock:
            if ext == 0:
                ext = utils.media_type_from_extension(file_name)
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO media VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    media_id,
                    post_id,
                    file_name,
                    uid,
                    type,
                    ext,
                    size,
                    duration,
                    json.dumps(extra_data),
                ),
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
            logger.log("Clear query cache.", verbose=1)
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
    def get_a_video(
        self, anchor=None, on_anchor=False, sort_type="new", fav=False, uid=None
    ):
        sql = f"""SELECT m.media_id FROM media m
            INNER JOIN posts p ON m.post_id = p.post_id
            WHERE m.ext = 1"""
        count_sql = f"""SELECT COUNT(*) FROM media m
            INNER JOIN posts p ON m.post_id = p.post_id
            WHERE m.ext = 1"""
        if not anchor:
            anchor = ""
        if "'" in anchor:
            anchor = anchor.replace("'", "''")
        if anchor:
            if on_anchor:
                sql += f" AND m.media_id = '{anchor}'"
                sort_type = "new"
            elif sort_type != "random":
                sql += f" AND m.media_id < '{anchor}'"
        # not implementing "fav" filter
        # if fav:
        #     sql += " AND p.fav = 1"
        if uid:
            sql += f" AND p.uid = '{uid}'"
            count_sql += f" AND p.uid = '{uid}'"

        logger.log("Executing SQL for counting videos:", count_sql, verbose=2)
        c_s = time.time()
        count_res = self.raw_query(count_sql)
        count = count_res[0][0] if count_res else 0
        c_e = time.time()
        logger.log(f"Counted {count} videos in {c_e - c_s:.4f} seconds.", verbose=1)

        if sort_type == "new":
            sql += " ORDER BY p.time DESC LIMIT 1"
        elif sort_type == "top":
            sql += " ORDER BY p.likes DESC LIMIT 1"
        elif sort_type == "random":
            offset = random.randint(0, max(0, count - 1))
            sql += f" LIMIT 1 OFFSET {offset}"

        logger.log("Executing SQL for get_a_video:", sql, verbose=2)
        res = self.raw_query(sql)
        logger.log("get_a_video result:", res, verbose=2)
        logger.log(f"Total videos matching criteria: {count}", verbose=1)
        if len(res) > 0:
            return res[0][0], count
        elif anchor:
            logger.log(
                f"No more videos found with anchor {anchor}, try returning without anchor, should be the first video."
            )
            return self.get_a_video(anchor=None, sort_type=sort_type, fav=fav, uid=uid)
        return None, count

    @utils.time_it
    def query_post_by_text(
        self,
        text_content: str,
        offset: int,
        limit: int,
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
                [
                    i.lstrip("u/").strip()
                    for i in re.split(r"[\s_\\/,.-]+", text_content)
                    if i and i != " "
                ]
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
        res = [r[0] for r in res]
        res = utils.remove_duplicate_with_order(res)
        self.cached_query_words[(words, offset, limit, sort_type)] = (
            time.time(),
            res,
            count,
        )
        return res, count

    @utils.time_it
    def get_new(self, start_idx, limit, uid="", media_only=False):
        sql = f"""SELECT p.post_id FROM posts p"""
        count_sql = f"""SELECT COUNT(*) FROM posts p"""
        conditions = []
        if media_only:
            sql += " INNER JOIN media m ON p.post_id = m.post_id"
            count_sql += " INNER JOIN media m ON p.post_id = m.post_id"
            conditions.append("m.post_id IS NOT NULL")
        if uid:
            conditions.append(f"p.uid = '{uid}'")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
            count_sql += " WHERE " + " AND ".join(conditions)
        sql += f" ORDER BY time DESC LIMIT {limit} OFFSET {start_idx}"
        count = self.raw_query(count_sql)[0][0] if self.raw_query(count_sql) else 0
        res = self.raw_query(sql)
        res = utils.remove_duplicate_with_order([row[0] for row in res])
        return res, count

    @utils.time_it
    def get_top(self, start_idx, limit, uid="", media_only=False):
        sql = f"""SELECT p.post_id FROM posts p"""
        count_sql = f"""SELECT COUNT(*) FROM posts p"""
        conditions = []
        if media_only:
            sql += " INNER JOIN media m ON p.post_id = m.post_id"
            count_sql += " INNER JOIN media m ON p.post_id = m.post_id"
            conditions.append("m.post_id IS NOT NULL")
        if uid:
            conditions.append(f"p.uid = '{uid}'")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
            count_sql += " WHERE " + " AND ".join(conditions)
        sql += f" ORDER BY likes DESC LIMIT {limit} OFFSET {start_idx}"
        count = self.raw_query(count_sql)[0][0] if self.raw_query(count_sql) else 0
        res = self.raw_query(sql)
        res = utils.remove_duplicate_with_order([row[0] for row in res])
        return res, count

    def get_random_with_time_range(self, limit, start_time, end_time):
        res = []
        attempts = 0
        max_attempts = limit * 3
        while len(res) < limit and attempts < max_attempts:
            random_time = random.uniform(start_time, end_time)
            sql = f"""
                SELECT post_id 
                FROM posts 
                WHERE time >= {random_time}
                ORDER BY time, post_id 
                LIMIT 1
            """
            logger.log("Executing SQL for get_random:", sql, verbose=2)
            row = self.raw_query(sql)
            if row:
                pid = row[0][0]
                if pid not in res:
                    res.append(pid)
            attempts += 1
        return res

    @utils.time_it
    def get_random(self, limit):
        # Get the time range
        row = self.raw_query("SELECT MIN(time), MAX(time) FROM posts")
        min_time, max_time = row[0]
        min_time = (2012 - 1970) * 365 * 24 * 3600  # (2012-01-01)
        logger.log(limit, min_time, max_time, verbose=2)

        res = []
        # To deal with biased distribution of posts over time.
        timestamp_oldest = min_time
        res += self.get_random_with_time_range(
            int(limit / 3), timestamp_oldest, max_time
        )
        timestamp_1year = time.time() - 1 * 365 * 24 * 3600
        res += self.get_random_with_time_range(
            int(limit / 3), timestamp_1year, max_time
        )
        timestamp_4years = time.time() - 4 * 365 * 24 * 3600
        res += self.get_random_with_time_range(
            int(limit / 3), timestamp_4years, max_time
        )
        random.shuffle(res)
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
        elif config.allow_external_url_preview:
            self.title, self.description, self.thumbnail = utils.probe_url(self.url)
            db.insert_or_update_url(
                self.url, self.title, self.description, self.thumbnail
            )
            db.commit()
        else:
            logger.log(
                f"Probing external URL is disabled, cannot probe {self.url}",
                type="warning",
            )
            self.title = "External Link"
            self.description = "URL preview is disabled in config."
            self.thumbnail = ""


class Post:
    def __init__(self, post_id, user_name, type):
        self.post_id = post_id
        if not "@" in post_id:
            if type:
                self.post_id = f"{post_id}@{type}"
            else:
                raise ValueError(
                    f"post_id must contain type suffix, e.g. '12345@x' user: {user_name}, post_id: {post_id}"
                )
        self.post_id_inner = post_id.split("@")[0]
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
        self.reply_to_id = ""
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
        self.has_attachment = False
        self.medias = []
        self.attachments = []
        self.extra_data = dict()
        self.isplaceholder = False

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
        self.reply_to_id = f"{self.reply_to.split('@')[0]}@{self.type}" if "@" in self.reply_to else ""
        self.real_user = row[13]
        self.alt = row[14]
        if self.alt:
            self.alts = self.alt.split("<sep>")
        self.text_content, link = utils.embed_hyperlink(self.type, row[1])
        if link:
            if utils.check_allowed_to_embed(link):
                self.embed = self.embed or link
                logger.log(
                    f"Post {self.post_id} has embed link: {self.embed}",
                    type="attention",
                    verbose=3,
                )
            elif (
                utils.check_allowed_to_probe(link) and config.allow_external_url_preview
            ):
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
                self.post_id_inner = str(json["tweet_id"])
                self.post_id = f"{self.post_id_inner}@x"
                self.text_content = json["content"]
                if not self.user_name:
                    self.user_name = json["author"]["name"].lower()
                self.nick = json["author"]["nick"]
                self.time = json["date"]  # eg. 2026-02-07 04:09:32
                dt = datetime.strptime(self.time, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                self.time = int(dt.timestamp())
                self.url = f"https://x.com/{self.user_name}/status/{self.post_id_inner}"
                self.likes = json["favorite_count"]
                self.reposts = json["retweet_count"]
                self.comments = json["reply_count"]

                self.isreply = "reply_to" in json
                reply_id = json.get("reply_id", "")
                reply_to_user = json.get("reply_to", "")
                if reply_id and reply_to_user:
                    self.reply_to = f"{reply_id}@{reply_to_user.lower()}"
            elif self.type == "bsky":
                self.post_id_inner = str(json["post_id"])
                self.post_id = f"{self.post_id_inner}@bsky"
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
                self.url = f"https://bsky.app/profile/{self.user_name}/post/{self.post_id_inner}"
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
                self.post_id_inner = json["id"]
                self.post_id = f"{self.post_id_inner}@reddit"
                self.text_content = (
                    f"<span class='rdt_title'>{json['title']}</span>{json['selftext']}"
                )
                self.user_name = json["subreddit"].lower()
                self.nick = self.user_name
                self.time = int(json["created_utc"])
                self.url = f"https://reddit.com/r/{self.user_name}/comments/{self.post_id_inner}"
                self.likes = json["score"]
                self.reposts = 0
                self.comments = json["num_comments"]
                self.isreply = False
                self.real_user = json["author"]
            elif self.type == "fa":
                self.post_id_inner = str(json["id"])
                self.post_id = f"{self.post_id_inner}@fa"
                self.text_content = f"<span class='rdt_title'>{json['title']}</span>{json['description']}"
                self.user_name = json["user"].lower()
                self.nick = json.get("artist", self.user_name)
                try:
                    self.time = json["date"]  # eg. 2022-03-10 10:09:11
                    self.time = int(
                        time.mktime(time.strptime(self.time, "%Y-%m-%d %H:%M:%S"))
                    )
                except:
                    self.time = 0
                if (
                    json.get("subcategory") == "journals"
                    or json.get("category") == "journals"
                ):
                    self.url = (
                        f"https://www.furaffinity.net/journal/{self.post_id_inner}/"
                    )
                else:
                    self.url = f"https://www.furaffinity.net/view/{self.post_id_inner}/"
                self.likes = json.get("favorites", 0)
                self.reposts = 0
                self.comments = json.get("comments", 0)
                self.isreply = False
                self.real_user = self.user_name
            elif self.type == "patreon":
                self.post_id_inner = str(json["id"])
                self.post_id = f"{self.post_id_inner}@patreon"
                title = json.get("title", "")
                text = json.get("content", "") or json.get("substring", "")
                self.text_content = f"<span class='rdt_title'>{title}</span>{text}"
                self.user_name = json.get("user_name", "").lower() or self.user_name
                self.nick = self.user_name
                self.time = json.get("published", "")  # eg. 2025-11-27T05:11:59
                try:
                    self.time = int(
                        time.mktime(time.strptime(self.time, "%Y-%m-%dT%H:%M:%S"))
                    )
                except:
                    try:
                        # eg. 2025-11-27T05:11:59.123000
                        self.time = int(
                            time.mktime(
                                time.strptime(self.time, "%Y-%m-%dT%H:%M:%S.%f")
                            )
                        )
                    except:
                        self.time = 0
                self.url = f"https://www.patreon.com/posts/{self.post_id_inner}"
                self.likes = json.get("likes", 0)
                self.reposts = 0
                self.comments = 0
                self.isreply = False
                self.real_user = json.get("user", self.user_name)
        except Exception as e:
            logger.log(f"Error loading post from JSON: {e}", type="error")
            logger.log(json, type="error")
            self.json_error = True
        # Set uid after user_name and type are determined
        self.uid = f"{self.user_name}@{self.type}"
        self.save_to_db()

    def init_medias(self):
        self.medias = []
        self.attachments = []
        for row in db.query_rows(
            selected_table="media", key="post_id", value=self.post_id
        ):
            media_id = row[0]
            media = Media(media_id, self.post_id, self.user_name, self.type)
            media.load_from_db()
            if utils.media_type_from_extension(media.file_name) > utils.FLASH:
                self.has_attachment = True
                self.attachments.append(media)
            else:
                self.medias.append(media)
        self.medias = natsort.natsorted(self.medias, key=lambda x: x.media_id)
        logger.log(
            f"Initialized medias for post {self.post_id}, found {len(self.medias)} medias and {len(self.attachments)} attachments.",
            verbose=2,
        )

    def init_embed(self):
        if self.embed:
            self.embed = (
                self.embed.replace("https://", "").replace("http://", "").strip("/")
            )
            if self.embed == self.url.replace("https://", "").replace(
                "http://", ""
            ).strip("/"):
                # If embed is same as url, skip to avoid infinite loop
                self.embed = ""
                return
            if self.embed.startswith("at://"):
                post_id = self.embed.split("/")[-1] + "@bsky"
                user_name = self.embed.split("/")[-3]
                type_ = "bsky"
            elif (
                "furaffinity.net/view/" in self.embed
                or "furaffinity.net/journal/" in self.embed
            ):
                post_id = self.embed.split("?")[0].strip("/").split("/")[-1] + "@fa"
                user_name = ""
                type_ = "fa"
            elif "/status/" in self.embed and (
                "x.com" in self.embed or "twitter.com" in self.embed
            ):
                post_id = self.embed.split("/")[-1] + "@x"
                user_name = self.embed.split("/")[-3]
                type_ = "x"
            elif "/post/" in self.embed and "bsky.app" in self.embed:
                post_id = self.embed.split("/")[-1] + "@bsky"
                user_name = self.embed.split("/")[-3]
                type_ = "bsky"
            else:
                self.embed = ""
                return
            self.embed_obj = Post(post_id, user_name, type_)
            self.embed_obj.is_external = not self.embed_obj.load_from_db()
            if not self.embed_obj.is_external:
                self.embed_obj.init_medias()
            self.embed_obj.concat_url()
            if "furaffinity.net/journal/" in self.embed:
                self.embed_obj.url = f"https://www.furaffinity.net/journal/{self.embed_obj.post_id_inner}/"

    def concat_url(self):
        if self.type == "x":
            self.url = f"https://x.com/{self.user_name}/status/{self.post_id_inner}"
        elif self.type == "bsky":
            self.url = (
                f"https://bsky.app/profile/{self.user_name}/post/{self.post_id_inner}"
            )
        elif self.type == "reddit":
            self.url = (
                f"https://reddit.com/r/{self.user_name}/comments/{self.post_id_inner}"
            )
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/view/{self.post_id_inner}/"

    def get_time_str(self):
        try:
            now = time.time()
            if now - self.time < 60:
                return "Just now"
            elif now - self.time < 3600:
                return f"{int((now - self.time) / 60)} minutes ago"
            elif now - self.time < 86400:
                return f"{int((now - self.time) / 3600)} hours ago"
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.time))
        except Exception as e:
            logger.log(
                f"Error formatting time for post {self.post_id}: {e}", type="error"
            )
            logger.log(f"User: {self.user_name}, Type: {self.type}", type="error")
            logger.log(f"Post time value: {self.time}", type="error")
            logger.log(traceback.format_exc(), type="error")
            return "Unknown time"


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
        self.extra_data = dict()

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
                self.extra_data = json.loads(row[10]) if row[10] else dict()
                if not self.nick:
                    self.nick = self.user_name
                self.concat_url()
                return True
            except Exception as e:
                logger.log(
                    f"Error loading user {self.user_name} from database: {e}",
                    type="error",
                )
                logger.log(rows, type="error")
                return False
        return False

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
            self.extra_data,
        )

    def load_from_json(self, json, use_fs_modified_time=False):
        if self.placeholder:
            return
        if self.type == "x":
            self.nick = json["author"]["nick"]
            self.udid = self.user_name
            self.avatar = json["author"]["profile_image"]
            self.banner = json["author"].get("profile_banner", "")
            self.description = json["author"].get("description", "")
            self.extra_data["url"] = (
                json["author"]
                .get("url", "")
                .replace("http://", "")
                .replace("https://", "")
            )
            self.extra_data["location"] = json["author"].get("location", "")
        elif self.type == "bsky":
            self.nick = json["author"]["displayName"]
            self.udid = json["author"]["did"]
            self.avatar = json["author"].get("avatar", "")
            self.banner = json["user"].get("banner", "")
            self.description = json["user"].get("description", "")
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
        elif self.type == "patreon":
            self.nick = self.user_name
            if "public_id" in json:
                self.udid = json["id"]
            else:
                self.udid = json.get("user", self.user_name)
            service = json["service"]
            self.extra_data["service"] = service
            if service in ["patreon", "fanbox"] and re.match(r"\d+", self.udid):
                self.avatar = (
                    f"https://img.{config.kemono_proxy}/icons/{service}/{self.udid}"
                )
                self.banner = (
                    f"https://img.{config.kemono_proxy}/banners/{service}/{self.udid}"
                )
                self.description = f"{service.capitalize()} user {self.user_name}."
            elif service == "onlyfans":
                self.avatar = (
                    f"https://img.{config.coomer_proxy}/icons/{service}/{self.udid}"
                )
                self.banner = (
                    f"https://img.{config.coomer_proxy}/banners/{service}/{self.udid}"
                )
                self.description = f"OnlyFans user {self.user_name}."
            else:
                self.avatar = ""
                self.banner = ""
                self.description = ""
        if use_fs_modified_time:
            self.update_time = os.path.getmtime(
                os.path.join(config.fs_bases[self.type], self.user_name)
            )
        else:
            self.update_time = time.time()
        if not self.banner:
            logger.log(
                f"warning: user {self.user_name} has no banner.",
                type="warning",
            )
        if not self.description:
            logger.log(
                f"warning: user {self.user_name} has no description.",
                type="warning",
            )
        self.concat_url()
        self.save_to_db()

    def concat_url(self):
        self.url = self.uid
        if self.type == "x":
            self.url = f"https://x.com/{self.user_name}"
        elif self.type == "bsky":
            self.url = f"https://bsky.app/profile/{self.user_name}"
        elif self.type == "reddit":
            self.url = f"https://reddit.com/r/{self.user_name}"
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/user/{self.user_name}"
        elif self.type == "patreon":
            service = self.extra_data.get("service", "")
            logger.log(f"User data: {self.extra_data}", verbose=2)
            if service == "patreon":
                self.url = f"https://www.patreon.com/{self.user_name}"
                self.extra_data["party_url"] = (
                    f"https://{config.kemono_proxy}/patreon/user/{self.udid}"
                )
            elif service == "fanbox":
                self.url = f"https://{self.user_name}.fanbox.cc/"
                self.extra_data["party_url"] = (
                    f"https://{config.kemono_proxy}/fanbox/user/{self.udid}"
                )
            elif service == "onlyfans":
                self.url = f"https://onlyfans.com/{self.user_name}"
                self.extra_data["party_url"] = (
                    f"https://{config.coomer_proxy}/onlyfans/user/{self.udid}"
                )

    def get_update_time_str(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.update_time))


class Media:
    def __init__(self, media_id, post_id, user_name, type):
        self.media_id = media_id
        self.post_id = post_id
        self.user_name = user_name
        self.type = type
        self.uid = f"{user_name}@{type}" if user_name and type else None
        self.size = 0
        self.duration = 0
        self.duration_str = "00:00"
        self.extra_data = dict()
        self.media_type = utils.UNKNOWN

    def save_to_db(self):
        # print(f"Saving media {self.media_id} to database with post_id {self.post_id}, file_name {self.file_name}, uid {self.uid}, type {self.type}, size {self.size}, duration {self.duration}")
        db.insert_or_update_media(
            self.media_id,
            self.post_id,
            self.file_name,
            self.uid,
            self.type,
            self.media_type,
            self.size,
            self.duration,
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
            self.file_name = self.file_name
            self.uid = row[3]
            # Extract user_name and type from uid if not already set
            if self.uid and "@" in self.uid:
                parts = self.uid.rsplit("@", 1)
                if not self.user_name:
                    self.user_name = parts[0]
                if not self.type:
                    self.type = parts[1]
            self.type = row[4]
            self.size = row[6]
            self.duration = row[7]
            self.duration_str = utils.format_duration(self.duration)
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
            self.media_type = utils.media_type_from_extension(self.file_name)
        except Exception as e:
            logger.log("Error:", e, type="error")
            logger.log("Rows:", rows, type="error")
            return False
        return True

    def get_size_str(self):
        return utils.format_size(self.size)


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
def scan_for_users(type, user_name=None, force=False):
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
            if (not user.load_from_db(True)) or len(user_names) == 1 or force:
                # user not found in database, create a new entry
                # select the first json file
                json_files = []
                if type == "fa":
                    if os.path.exists(os.path.join(fs_base, user_name, "user.json")):
                        json_files = ["user.json"]
                    elif os.path.exists(
                        os.path.join(fs_base, user_name, "user.json.gz")
                    ):
                        json_files = ["user.json.gz"]
                elif (
                    type in ["x", "bsky", "reddit", "patreon"]
                    and not user_name == "reddit_users"
                ):
                    file_list = os.listdir(os.path.join(fs_base, user_name))
                    json_files = [
                        f
                        for f in file_list
                        if f.endswith(".json") or f.endswith(".json.gz")
                    ]
                    json_files = natsort.natsorted(json_files, reverse=True)
                if len(json_files) > 0:
                    logger.log(f"found user json file: {json_files[0]}")
                    if json_files[0].endswith(".json.gz"):
                        file_handle = gzip.open(
                            os.path.join(fs_base, user_name, json_files[0]),
                            "rt",
                            encoding="utf-8",
                        )
                    else:
                        file_handle = open(
                            os.path.join(fs_base, user_name, json_files[0]),
                            "r",
                            encoding="utf-8",
                        )
                    with file_handle as f:
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


def check_for_missing_media(uid, remove=False):
    rows = db.query_rows("media", "uid", uid, ignore_cache=True)
    for row in rows:
        media_id = row[0]
        file_name = row[2]
        uid = row[3]
        user_name, type = uid.split("@")
        fs_base = config.fs_bases[type]
        media_path = os.path.join(fs_base, user_name, file_name)
        if not os.path.exists(media_path):
            logger.log(
                f"Media file {media_path} is missing for media_id {media_id}, post_id {row[1]}, uid {uid}",
                type="warning",
            )
            if remove:
                db.raw_query(
                    ("DELETE FROM media WHERE media_id = ?", (media_id,)),
                    ignore_cache=True,
                )
                logger.log(
                    f"Removed media_id {media_id} from database.", type="warning"
                )


current_scan_user = None


def scan_for_posts(type, user_name=None, force=False):
    global current_scan_user
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
        current_scan_user = user_name
        uid = f"{user_name}@{type}"
        user_fs_path = os.path.join(fs_base, user_name)
        if not os.path.exists(user_fs_path) or not os.path.isdir(user_fs_path):
            logger.log(user_name, "does not exists!")
            continue

        logger.log(
            f"[{cnt+1}/{len(user_names)}] scanning for posts of user {user_name}"
        )

        filelist = os.listdir(user_fs_path)
        if force:
            check_for_missing_media(uid, True)
        # check if is file
        filelist = [
            f for f in filelist if os.path.isfile(os.path.join(fs_base, user_name, f))
        ]
        filelist = [f for f in filelist if f.lower() not in utils.exclude_files]

        # read json files in the user directory
        json_files = [
            f for f in filelist if f.endswith(".json") or f.endswith(".json.gz")
        ]
        regex_map = {
            "x": {
                "file_patterns": [r"\d+.+json"],
                "id_pattern": [r"(\d+)", r"([a-zA-Z0-9]+)"],
            },
            "bsky": {
                "file_patterns": [r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}.+\.json"],
                "id_pattern": [
                    r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_([^_]+).+",
                    r"([a-zA-Z0-9]+)",
                ],
            },
            "reddit": {
                "file_patterns": [r".+json"],
                "id_pattern": [r"([a-zA-Z0-9]+)"],
            },
            "fa": {
                "file_patterns": [r"\d+"],
                "id_pattern": [r"(\d+)", r"([a-zA-Z0-9]+)"],
            },
            "patreon": {
                "file_patterns": [r"\d{4,20}"],
                "id_pattern": [r"(\d{4,20})", r"([a-zA-Z0-9]+)"],
            },
        }
        post_files = []
        patterns = regex_map[type]
        for pat in patterns["file_patterns"]:
            post_files += [f for f in json_files if re.match(pat, f)]
        for post_file in post_files:
            id_match = [re.match(pat, post_file) for pat in patterns["id_pattern"]]
            id_match = [m for m in id_match if m]
            if not id_match:
                logger.log(
                    f"warning: filename {post_file} does not match any id pattern for type {type}, skipped.",
                    type="warning",
                )
                continue
            post_id = id_match[0].group(1) + "@" + type
            post = Post(post_id, user_name, type)
            if (not post.load_from_db()) or force:
                json_file = os.path.join(fs_base, user_name, post_file)
                logger.log("Reading json file:", json_file, verbose=3)
                if post_file.endswith(".json.gz"):
                    file_handle = gzip.open(json_file, "rt", encoding="utf-8")
                else:
                    file_handle = open(json_file, "r", encoding="utf-8")
                with file_handle as f:
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
        #
        # scan for media
        #
        logger.log(
            f"[{cnt+1}/{len(user_names)}] scanning for media of user {user_name}"
        )
        for root, dirs, files in os.walk(os.path.join(fs_base, user_name)):
            for media_file in files:
                if utils.media_type_from_extension(media_file) == utils.UNKNOWN:
                    continue
                if media_file.lower() in utils.exclude_files:
                    continue
                media_full_path = os.path.join(root, media_file)
                rel_path = os.path.relpath(
                    media_full_path, os.path.join(fs_base, user_name)
                )
                if rel_path.startswith("."):
                    continue
                if type in ["x", "bsky", "reddit", "patreon"]:
                    media_id = f"{rel_path.replace(os.sep, '_')}@{user_name}@{type}"
                    id_match = [
                        re.match(pat, media_file) for pat in patterns["id_pattern"]
                    ]
                    id_match = [m for m in id_match if m]
                    if id_match:
                        related_post_id = f"{id_match[0].group(1)}@{type}"
                    else:
                        # strip file extension and use the remaining as media id
                        related_post_id = media_id.split(".")[0] + "@" + type
                        logger.log(
                            "warning: no post_id found in filename:",
                            media_file,
                            type="warning",
                        )
                elif type == "fa":
                    media_id = media_file
                    if media_file.endswith("_thumb.jpg"):
                        base_media_id = media_file[:-10]
                    else:
                        base_media_id = media_file
                    related_json_path = os.path.join(
                        fs_base, user_name, base_media_id + ".json"
                    )
                    if os.path.exists(related_json_path):
                        with open(
                            related_json_path,
                            "r",
                            encoding="utf-8",
                        ) as f:
                            try:
                                post_json = json.load(f)
                                related_post_id = str(post_json["id"]) + "@fa"
                            except Exception as e:
                                logger.log(e)
                                related_post_id = f"{media_file}@{user_name}@fa"
                                logger.log(
                                    "Error loading:",
                                    related_json_path,
                                    type="error",
                                )
                                if debug_mode:
                                    raise e
                    else:
                        related_post_id = f"{media_file}@{user_name}@fa"
                        logger.log(
                            "warning: no json file found for media:",
                            related_json_path,
                            type="warning",
                        )
                else:
                    logger.log(f"Unknown type {type} for media scanning.", type="error")
                    continue
                # test if related post exists
                post = Post(related_post_id, user_name, type)
                if not post.load_from_db(True):
                    logger.log(
                        f"warning: media {media_id} has no related post {related_post_id} in database",
                        type="warning",
                    )
                    # create a dummy post
                    post.text_content = media_file
                    post.user_name = user_name
                    guessed_timestamp = re.match(
                        r"(?:^|[\._ ])(\d{10})[\._ ]", media_id
                    )
                    if guessed_timestamp and time.gmtime() > time.gmtime(
                        int(guessed_timestamp.group(1))
                    ):
                        post.time = int(guessed_timestamp.group(1))
                    else:
                        post.time = int(os.path.getmtime(media_full_path))
                    post.type = type
                    if type == "reddit" and re.match(r"[a-zA-Z0-9]{6,8}_\d", media_id):
                        dummy_id = media_id.split("_")[0]
                        post.url = (
                            f"https://reddit.com/r/{user_name}/comments/{dummy_id}"
                        )
                    else:
                        post.url = ""
                    post.likes = 0
                    post.reposts = 0
                    post.comments = 0
                    post.save_to_db()
                media = Media(media_id, related_post_id, user_name, type)
                if (not media.load_from_db()) or force:
                    media.file_name = rel_path
                    try:
                        media.size = os.path.getsize(media_full_path)
                    except:
                        media.size = 0
                    if (
                        utils.media_type_from_extension(media_file) == utils.VIDEO
                        and not media.duration
                    ):
                        logger.log(f"Probing video duration for {media_file}")
                        media.duration = utils.probe_video_duration(media_full_path)
                    media.save_to_db()
        db.commit()
    current_scan_user = None
    db.clear_cache()


def get_users():
    rows = db.raw_query("SELECT uid, type FROM users")
    users = {"new": [], "name": []}
    for row in rows:
        # uid = row[0]
        # print(row)
        user = User(*row[0].split("@"))
        user.load_from_db()
        users["new"].append(user)
        users["name"].append(user)
    users["new"].sort(key=lambda u: u.update_time, reverse=True)
    users["name"].sort(key=lambda u: u.uid)
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


all_users = {}

query_cache_size_limit = 2000
query_cache = dict()
query_cache_media = dict()


@utils.time_it
def get_fav():
    res = db.query_rows(selected_table="fav", key="", value="", ignore_cache=True)
    res = [r[0] for r in res]
    return res


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
    db.conn.close()
