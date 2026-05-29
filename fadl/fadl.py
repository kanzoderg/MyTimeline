# File: fadl.py
# Furaffinity Downloader

import requests
import re, os, time, json
import argparse
from bs4 import BeautifulSoup as Soup

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Referer": "https://www.furaffinity.net/",
}

current_dir = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description="Furaffinity Downloader")
parser.add_argument("url", help="URL of the Furaffinity user, post or journal to download")
parser.add_argument(
    "--proxy", default="", help="Proxy URL (e.g., http://127.0.0.1:10808)"
)
parser.add_argument(
    "-o", "--output", help="Directory to save downloaded content", default="downloads"
)
parser.add_argument("-f", "--force", action="store_true", help="Force fetch all.")
parser.add_argument(
    "--cookies",
    default=os.path.join(current_dir, "cookies.txt"),
    help="Cookies for authentication",
)
parser.add_argument(
    "--user-agent",
    default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    help="Custom User-Agent header",
)
parser.add_argument(
    "-ni",
    "--no-interactive",
    help="Run in non-interactive mode (no progress bars, minimal output)",
    action="store_true",
)

args = parser.parse_args()

cookies = {}
if args.cookies and os.path.exists(args.cookies):
    with open(args.cookies, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7:
                name = parts[5]
                value = parts[6]
                cookies[name] = value

# print("cookies:", cookies)

headers["User-Agent"] = args.user_agent

# print("Using headers:", headers)

if args.proxy:
    proxies = {"http": args.proxy, "https": args.proxy}
else:
    proxies = None
if args.url:
    args.url = args.url.strip().lower()


def get(url):
    retry_count = 3
    for attempt in range(retry_count):
        try:
            response = requests.get(
                url, cookies=cookies, headers=headers, proxies=proxies, timeout=10
            )
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(2)
            if attempt == retry_count - 1:
                print(f"All {retry_count} attempts failed for URL: {url}")
                print("=" * 50)
                print(
                    "FA might be currently under `Under Attack` mode, which blocks requests that look like bots. Please pass the captcha in your browser, then export the cookies.txt and upload it. If you are already using cookies, try refreshing them. If the problem persists, you may need to wait until the attack mode is lifted."
                )
                print("=" * 50)
                raise


exsisting_items = set()
user_info = {}


def scan_existing_items(user):
    global exsisting_items
    if exsisting_items:
        return
    user_path = os.path.join(args.output, user)
    if not os.path.exists(user_path):
        return
    print(f"Scanning existing items for user {user}...")
    print(f"Looking in {user_path}...")
    for filename in os.listdir(user_path):
        if filename.endswith(".json") and re.match(r"\d+", filename):
            try:
                file_path = os.path.join(user_path, filename)
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    exsisting_items.add(data["id"])
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
    print(f"Found {len(exsisting_items)} existing items for user {user}")
    # print(exsisting_items)


def put_user_info(user, no_overwrite=False):
    global user_info
    user_info_file = os.path.join(args.output, user, "user.json")
    if user_info:
        return
    if no_overwrite and os.path.exists(user_info_file):
        return
    url = f"https://www.furaffinity.net/user/{user}/"
    resp = get(url)
    soup = Soup(resp.text, "html.parser")
    display_name = soup.find(class_="js-displayName").text.strip()
    join_date = soup.find(class_="popup_date")["data-time"]
    banner_url = soup.find("site-banner").find("picture").img["src"]
    if banner_url.startswith("//"):
        banner_url = "https:" + banner_url
    avatar_url = soup.find("userpage-nav-avatar").find("img")["src"]
    if avatar_url.startswith("//"):
        avatar_url = "https:" + avatar_url
    with open("debug_user.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    description = soup.find(class_="userpage-profile").decode_contents()
    user_profile = soup.find(
        class_="userpage-layout-right-col-content"
    ).decode_contents()
    user_info = {
        "username": user,
        "display_name": display_name,
        "join_date": join_date,
        "banner_url": banner_url,
        "avatar_url": avatar_url,
        "description": description,
        "profile": user_profile,
    }
    os.makedirs(os.path.join(args.output, user), exist_ok=True)
    with open(user_info_file, "w", encoding="utf-8") as f:
        json.dump(user_info, f)
    print(f"Saved user info for {user}")


def is_image_url(url):
    return url.lower().endswith(
        (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".avif")
    )


class Item:
    def __init__(
        self,
        link,
        id_,
        category,
        user="",
        artist="",
        title="",
        description="",
        timestamp=0,
        comments=0,
    ):
        self.user = user
        self.artist = artist
        self.category = category
        self.title = title
        self.description = description
        self.link = link
        if self.link.startswith("//"):
            self.link = "https:" + self.link
        elif self.link.startswith("/"):
            self.link = "https://www.furaffinity.net" + self.link
        elif not self.link.startswith("http"):
            print(f"Invalid link: {self.link}")
            self.available = False
            return
        print(f"Processing item: [{self.user}][{ id_}] {self.link}")
        self.id_ = id_
        self.timestamp = timestamp
        self.date = ""
        if self.timestamp:
            self.date = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)
            )
        self.tags = []
        self.views = 0
        self.comments = comments
        self.favorites = 0
        self.rating = ""
        self.filename = f"{id_}"
        self.image_url = ""
        self.thumbnail_url = ""
        self.available = True
        self.already_exists = False
        if user:
            put_user_info(self.user)
            scan_existing_items(user)
            if id_ in exsisting_items and category in ["gallery", "scraps"]:
                self.already_exists = True
                print(f"Already exists: [{ self.id_}] {self.user}")

    def parse(self):
        if not self.available:
            return
        if self.already_exists and not args.force:
            return
        if (
            self.category == "journals"
            and self.user
            and self.title
            and self.description
        ):
            print(f"Skipping fetch for journal item {self.id_}")
            return
        resp = get(self.link)
        # dump html for debugging
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        soup = Soup(resp.text, "html.parser")
        # print(soup.text)
        if any(
            (
                "The owner of this page has elected to make it available to registered users only."
                in soup.text,
                "To view this submission you must log in" in soup.text,
            )
        ):
            print(f"Item {self.id_} is restricted to registered users only.")
            print(
                "Check your cookies and make sure you have access to this item in your browser."
            )
            self.available = False
            return
        if self.category in ["gallery", "scraps"]:
            if not self.user:
                try:
                    self.user = (
                        soup.find(class_="c-usernameBlockSimple")
                        .a["href"]
                        .strip("/")
                        .split("/")[-1]
                    )
                    self.artist = soup.find(
                        class_="c-usernameBlockSimple"
                    ).a.text.strip()
                except Exception as e:
                    print(f"l184 Failed to parse user for item {self.id_}: {e}")
                    self.available = False
                    return
            put_user_info(self.user)
            scan_existing_items(self.user)
            if (
                self.id_ in exsisting_items
                and self.category in ["gallery", "scraps"]
                and not args.force
            ):
                print(f"l266: Already exists: [{ self.id_}] {self.user}")
                self.already_exists = True
                return
            self.title = soup.find(class_="submission-title").text.strip()
            self.image_url = soup.find(class_="button", string="Download")["href"]
            self.thumbnail_url = soup.find(id="submissionImg")
            if self.thumbnail_url:
                self.thumbnail_url = self.thumbnail_url["src"]
            self.filename = self.image_url.split("/")[-1]
            self.timestamp = int(soup.find(class_="popup_date")["data-time"])
            self.date = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)
            )
            self.description = soup.find(
                class_="submission-description-text"
            ).decode_contents()
            for tag_elem in soup.find_all(class_="tags"):
                tag_text = tag_elem.text.strip()
                self.tags.append(tag_text)
            self.views = soup.find(title="Views").div.text.strip()
            self.comments = soup.find(title="Comments").div.text.strip()
            self.favorites = soup.find(title="Favorites").div.text.strip()
            self.rating = "N/A"
        elif self.category == "journals":
            self.user = (
                soup.find(class_="c-usernameBlock__displayName")["href"]
                .strip("/")
                .split("/")[-1]
            )
            self.artist = soup.find(class_="c-usernameBlock__displayName").text.strip()
            self.title = (
                soup.find(class_="journal-title")
                or soup.find(id="c-journalTitleTop__subject")
            ).text.strip()
            self.filename = f"{self.id_}"
            self.timestamp = int(soup.find(class_="popup_date")["data-time"])
            self.date = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)
            )
            self.description = soup.find(class_="journal-content").decode_contents()
            self.views = 0
            self.comments = soup.find(class_="section-footer").span.text.strip()
            self.favorites = 0
            self.rating = "N/A"
        if self.image_url.startswith("//"):
            self.image_url = "https:" + self.image_url
        if self.thumbnail_url and self.thumbnail_url.startswith("//"):
            self.thumbnail_url = "https:" + self.thumbnail_url

    def fetch(self):
        if not self.available:
            return
        output_path = os.path.join(args.output, self.user)
        os.makedirs(output_path, exist_ok=True)
        file_path = os.path.join(output_path, self.filename)
        thumbnail_path = os.path.join(output_path, f"{self.filename}_thumb.jpg")
        if self.category in ["gallery", "scraps"] and not self.already_exists:
            if os.path.exists(file_path):
                print(f"Media file already exists: [{ self.id_}] {file_path}")
                self.already_exists = True
            else:
                resp = get(self.image_url)
                with open(file_path, "wb") as f:
                    f.write(resp.content)
                if not is_image_url(file_path) and self.thumbnail_url:
                    # If the downloaded file is not an image, download the thumbnail as well
                    resp = get(self.thumbnail_url)
                    with open(thumbnail_path, "wb") as f:
                        f.write(resp.content)
        print(f"Saving metadata to {file_path}.json")
        with open(file_path + ".json", "w", encoding="utf-8") as f:
            data = {
                "user": self.user,
                "artist": self.artist,
                "category": "furaffinity",
                "subcategory": self.category,
                "title": self.title,
                "link": self.link,
                "id": self.id_,
                "timestamp": self.timestamp,
                "date": self.date,
                "description": self.description,
                "tags": self.tags,
                "views": self.views,
                "favorites": self.favorites,
                "comments": self.comments,
                "rating": self.rating,
                "url": self.image_url,
                "filename": self.filename,
            }
            json.dump(
                data,
                f,
            )
        if not self.already_exists:
            print(f"Downloaded: {self.title}[{ self.id_}]")


class Pager:
    def __init__(self, user, category):
        self.user = user
        self.category = category
        self.base_url = f"https://www.furaffinity.net/{category}/{user}/"
        self.page = 1

    def items(self):
        if self.category in ["gallery", "scraps"]:
            return self._gallery_items()
        elif self.category == "journals":
            return self._journal_items()
        else:
            return []

    def _gallery_items(self):
        while True:
            print(f"Fetching page {self.page} of {self.user}'s {self.category}...")
            url = self.base_url + str(self.page) + "/"
            resp = get(url)
            soup = Soup(resp.text, "html.parser")
            artist = soup.find(class_="js-displayName").text.strip()
            for item in soup.find(id="gallery-gallery").find_all("figure"):
                link = item.find("a")["href"]
                title = item.find("figcaption").p.text.strip()
                id_ = link.strip("/").split("/")[-1]
                yield Item(
                    link, id_, self.category, self.user, artist=artist, title=title
                )
            if not soup.find("button", string="Next"):
                break
            self.page += 1

    def _journal_items(self):
        while True:
            url = self.base_url + str(self.page) + "/"
            print(f"Fetching page {self.page} of {self.user}'s {self.category}...")
            resp = get(url)
            soup = Soup(resp.text, "html.parser")
            artist = soup.find(class_="js-displayName").text.strip()
            for item in soup.find(class_="content").find_all("section"):
                title = item.find(class_="section-header").h2.text.strip()
                link = item.find(class_="section-footer").a["href"]
                id_ = link.strip("/").split("/")[-1]
                description = item.find(class_="section-body").decode_contents()
                timestamp = int(item.find(class_="popup_date")["data-time"])
                comments = (
                    item.find(class_="section-footer")
                    .find(class_="font-large")
                    .text.strip()
                )
                yield Item(
                    link,
                    id_,
                    self.category,
                    self.user,
                    artist,
                    title,
                    description,
                    timestamp,
                    comments,
                )
            if not soup.find("button", string="Older"):
                break
            self.page += 1


def main():
    skip_count = 0

    gallery_pattern = r"https://www\.furaffinity\.net/view/(\d+)"
    journal_pattern = r"https://www\.furaffinity\.net/journal/(\d+)"
    user_content_pattern = (
        r"https://www\.furaffinity\.net/(gallery|scraps|journals)/([\w\d_\-\.\~]+)"
    )
    user_pattern = r"https://www\.furaffinity\.net/user/([\w\d_\-\.\~]+)"
    if re.match(gallery_pattern, args.url):
        category = "gallery"
        id_ = args.url.strip("/").split("/")[-1]
        item = Item(args.url, id_, category)
        item.parse()
        item.fetch()
    elif re.match(journal_pattern, args.url):
        category = "journals"
        id_ = args.url.strip("/").split("/")[-1]
        item = Item(args.url, id_, category)
        item.parse()
        item.fetch()
    elif re.match(user_content_pattern, args.url):
        m = re.match(
            user_content_pattern,
            args.url,
        )
        category = m.group(1)
        user = m.group(2)
        pager = Pager(user, category)
        for item in pager.items():
            if item.already_exists:
                skip_count += 1
            if skip_count >= 20 and not args.force:
                print("Skipping remaining items due to consecutive existing items.")
                break
            item.parse()
            item.fetch()
    elif re.match(user_pattern, args.url):
        m = re.match(user_pattern, args.url)
        user = m.group(1)
        for category in ["gallery", "scraps", "journals"]:
            pager = Pager(user, category)
            for item in pager.items():
                if item.already_exists:
                    skip_count += 1
                if skip_count >= 20 and not args.force:
                    print("Skipping remaining items due to consecutive existing items.")
                    skip_count = 0
                    break
                item.parse()
                item.fetch()
    else:
        print("Invalid URL")


if __name__ == "__main__":
    main()
