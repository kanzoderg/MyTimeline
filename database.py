import sqlite3
import threading
import time
import json
import re
import random
import natsort
import traceback
import sys
import utils, logger

db = None

query_cache_size_limit = 2000
query_cache = dict()
query_cache_media = dict()


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
            cursor.execute("""CREATE TABLE IF NOT EXISTS users (
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
            )""")
            # ALTER TABLE users ADD extra_data TEXT
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_user_name ON users(user_name)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_udid ON users(udid)")

            # Posts table with indexes optimized for various query patterns
            cursor.execute("""CREATE TABLE IF NOT EXISTS posts (  
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
                reply_to TEXT,  
                reply_root TEXT,  
                real_user TEXT,  
                alt TEXT,
                extra_data TEXT,
                tags TEXT
            )""")
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
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_reply_root ON posts(reply_root)"
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
            cursor.execute("""CREATE TABLE IF NOT EXISTS media (  
                media_id TEXT PRIMARY KEY,  
                post_id TEXT,  
                file_name TEXT,  
                uid TEXT,  
                type TEXT,  
                ext INTEGER,  
                size INTEGER,  
                duration INTEGER,  
                extra_data TEXT
            )""")
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
            cursor.execute("""CREATE TABLE IF NOT EXISTS idmap (
                    numid INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id TEXT,
                    UNIQUE(post_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idmap_post_id ON idmap(post_id)")
            # Create FTS5 virtual table for full-text search on posts
            # Using standalone table (not content-less) for simpler synchronization
            cursor.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
                    post_id,
                    uid,
                    nick,
                    real_user,
                    text_content,
                    alt,
                    tags
                )""")
            # Create triggers to keep FTS table in sync with posts table
            # Uses idmap.numid as rowid for posts_fts to ensure uniqueness
            # This eliminates slow EXISTS checks - SQLite's rowid uniqueness handles deduplication
            cursor.execute(
                """CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
                    INSERT INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt, tags)
                    SELECT (SELECT numid FROM idmap WHERE post_id = new.post_id), new.post_id, new.uid, new.nick, COALESCE(new.real_user, ''), new.text_content, COALESCE(new.alt, ''), COALESCE(new.tags, '');
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

            cursor.execute("""CREATE TABLE IF NOT EXISTS user_group (
                group_id TEXT,
                group_name TEXT,
                uid TEXT,
                timestamp INTEGER,
                UNIQUE(uid, group_id)
                )""")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_group_uid ON user_group(uid)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_group_group_id ON user_group(group_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_group_timestamp ON user_group(timestamp)"
            )

            cursor.execute("""CREATE TABLE IF NOT EXISTS external_link_lookup (
                url TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                thumbnail TEXT
                )""")

            fav_cursor = self.fav_conn.cursor()
            fav_cursor.execute("""CREATE TABLE IF NOT EXISTS fav (
                post_id TEXT PRIMARY KEY,
                fav_time INTEGER
            )""")
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
        reply_to="",
        reply_root="",
        real_user="",
        alt="",
        extra_data={},
        tags_str="",
    ):
        if not "@" in post_id:
            raise ValueError(
                f"post_id must contain type suffix, e.g. '12345@x' user: {uid}, post_id: {post_id}"
            )
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    reply_to,
                    reply_root,
                    real_user,
                    alt,
                    json.dumps(extra_data),
                    tags_str,
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
        text_content = text_content.strip().lower()

        filters = []

        # Check for "mode:full" to use LIKE-based search
        use_full_mode = "mode:full" in text_content
        if "mode:full" in text_content:
            text_content = text_content.replace("mode:full", "").strip()
            filters.append("mode:full")

        # Check for "group:GROUPID" to filter by user group
        group_filter_match = re.search(r"group:([^\s]+)", text_content)
        group_id = None
        if group_filter_match:
            group_id = group_filter_match.group(1)
            text_content = re.sub(r"group:[^\s]+", "", text_content).strip()
            filters.append(f"group:{group_id}")

        text_content = text_content.replace("(", " ").replace(")", " ")

        words = tuple(
            set(
                [
                    i.lstrip("u/").strip()
                    for i in re.split(r"[\s_\/,.-]+", text_content)
                    if i and i != " "
                ]
            )
        )
        if (words, tuple(filters), offset, limit, sort_type) in self.cached_query_words:
            if (
                abs(
                    self.cached_query_words[
                        (words, tuple(filters), offset, limit, sort_type)
                    ][0]
                    - time.time()
                )
                > 1200
            ):
                logger.log("Clear outdated query cache.", verbose=1)
                self.cached_query_words = dict()
            else:
                logger.log("Use cached query for", words, verbose=1)
                return (
                    self.cached_query_words[
                        (words, tuple(filters), offset, limit, sort_type)
                    ][1],
                    self.cached_query_words[
                        (words, tuple(filters), offset, limit, sort_type)
                    ][2],
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

        if group_id:
            logger.log(f"Filtering search results by user group {group_id}", verbose=2)
            logger.log(f"Text content will be ignored for group filtering.", verbose=2)
            sql_query = f"""
                SELECT p.* FROM posts p
                INNER JOIN user_group ug ON p.uid = ug.uid
                WHERE ug.group_id = ?
                ORDER BY {order_by} DESC
                LIMIT {limit} OFFSET {offset}
            """
            params = (group_id,)
            logger.log(sql_query, params)
            res = self.raw_query(
                (sql_query, params),
                sort_reverse=sort_reverse,
            )

            # Count total results
            count_sql = f"""SELECT COUNT(*) FROM posts p
                INNER JOIN user_group ug ON p.uid = ug.uid  
                WHERE ug.group_id = ?
            """
            count_res = self.raw_query(
                (count_sql, params),
            )
            count = count_res[0][0] if count_res else 0

        elif use_full_mode:
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
        self.cached_query_words[(words, tuple(filters), offset, limit, sort_type)] = (
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

    def get_comments(self, root_id, type=None):
        if type == "x":
            pid = root_id.split("@")[0]
            root_id = f"{pid}@i"  # `i` is the placeholder username for comments in X, since X metadata doesn't include the actual username of the conversation root
        print("Getting comments for root_id:", root_id)
        sql = f"""SELECT post_id, uid FROM posts WHERE reply_root = '{root_id}'"""
        res = self.raw_query(sql)
        return [(row[0], row[1].split("@")[0]) for row in res]

    def commit(self):
        self.conn.commit()
        self.fav_conn.commit()

    def clear_cache(self):
        global query_cache, query_cache_media
        query_cache = dict()
        query_cache_media = dict()
        self.cached_query_words = dict()
        self.query_search_results_counter = dict()

    def remove_user(self, uid):
        with self.db_lock:
            logger.log(
                f"Removing user {uid} and all associated data from database.",
                type="warning",
            )
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM users WHERE uid = ?", (uid,))
            cursor.execute("DELETE FROM posts WHERE uid = ?", (uid,))
            cursor.execute("DELETE FROM media WHERE uid = ?", (uid,))
            cursor.execute("DELETE FROM user_group WHERE uid = ?", (uid,))
            cursor.close()

    def rename_user(self, old_uid, new_uid):
        with self.db_lock:
            logger.log(
                f"Renaming user {old_uid} to {new_uid} in database.", type="warning"
            )
            cursor = self.conn.cursor()
            cursor.execute("UPDATE posts SET uid = ? WHERE uid = ?", (new_uid, old_uid))
            cursor.execute("UPDATE media SET uid = ? WHERE uid = ?", (new_uid, old_uid))
            cursor.execute(
                "UPDATE user_group SET uid = ? WHERE uid = ?", (new_uid, old_uid)
            )

            # find if the new_uid already exists in users table
            cursor.execute("SELECT uid FROM users WHERE uid = ?", (new_uid,))
            if cursor.fetchone():
                logger.log(
                    f"User with uid {new_uid} already exists in users table, deleting old one.",
                    type="warning",
                )
                cursor.execute("DELETE FROM users WHERE uid = ?", (old_uid,))
            else:
                logger.log(
                    f"No existing user with uid {new_uid} found in users table, proceeding with rename.",
                    type="warning",
                )
                cursor.execute(
                    "UPDATE users SET uid = ? WHERE uid = ?", (new_uid, old_uid)
                )
            username = new_uid.split("@")[0]
            cursor.execute(
                "UPDATE users SET user_name = ? WHERE uid = ?", (username, new_uid)
            )
            cursor.close()

    def get_cursor(self, selected_db="main"):
        if selected_db == "fav":
            return self.fav_conn.cursor()
        else:
            return self.conn.cursor()
