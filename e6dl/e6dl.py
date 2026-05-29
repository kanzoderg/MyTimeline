# File: e6dl.py
# E621 downloader

import os, sys, re
import requests
from bs4 import BeautifulSoup as Soup
import json, time
import argparse
from urllib.parse import unquote

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Referer": "https://www.e621.net/",
}

current_dir = os.path.dirname(os.path.abspath(__file__))

artists_tags_exclude = [
    "sound_warning",
    "sound warning",
    "conditional_dnp",
    "conditional dnp",
    "epilepsy_warning",
    "epilepsy warning",
    "third-party_edit",
    "third-party edit",
    "avoid_posting",
    "avoid posting",
]

# set arguments
parser = argparse.ArgumentParser(description="E621 Downloader")
parser.add_argument("url", help="E621 post or search-with-tag URL")
parser.add_argument(
    "--proxy", default="", help="Proxy URL (e.g., http://127.0.0.1:10808)"
)
parser.add_argument(
    "-o", "--output", help="Directory to save downloaded content", default="downloads"
)
parser.add_argument(
    "--main-tag",
    help="Main tag to use when downloading from a search URL with multiple tags, also will be used as the directory name to save the downloaded content. If not provided, will ask user to choose interactively.",
)
parser.add_argument(
    "-ni",
    "--no-interactive",
    help="Run in non-interactive mode (no progress bars, minimal output)",
    action="store_true",
)

args = parser.parse_args()

cookies = {}

if os.path.exists(os.path.join(current_dir, "cookies.txt")):
    print("Loading cookies from cookies.txt...")
    with open(os.path.join(current_dir, "cookies.txt"), "r") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 7:
                name = parts[5]
                value = parts[6]
                cookies[name] = value

if os.path.exists(os.path.join(current_dir, "persistant_cookies.json")):
    print("Loading persistent cookies...")
    with open(os.path.join(current_dir, "persistant_cookies.json"), "r") as f:
        cookies.update(json.load(f))


proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
session = requests.Session()
session.headers.update(headers)
if cookies:
    session.cookies.update(cookies)

def sanitize_tag(tag):
    tag = unquote(tag)
    return tag.strip().replace(" ", "_").replace("@", "[at]").replace("/", "[slash]").replace("\\", "[backslash]")

def restore_tag(sanitized_tag):
    return sanitized_tag.replace("[at]", "@").replace("[slash]", "/").replace("[backslash]", "\\").replace("_", " ").strip()

def get(url):
    retry_count = 3
    for attempt in range(retry_count):
        try:
            response = session.get(
                url, cookies=cookies, headers=headers, proxies=proxies, timeout=10
            )
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(2)
            if attempt == retry_count - 1:
                raise


def accept_tos():
    if "tos_accepted" in session.cookies:
        print("TOS already accepted, cookies loaded.")
        return True
    print("Accepting TOS...")
    # get auth-token first
    url = "https://e621.net/"
    resp = get(url)
    soup = Soup(resp.text, "html.parser")
    auth_token = soup.find(id="tos-form").input.attrs["value"]
    print("Auth token:", auth_token)
    # get cookies after accepting TOS
    url = "https://e621.net/terms_of_use/accept"
    payload = "authenticity_token=" + auth_token + "&age=on&terms=on&state=accepted"
    resp = session.post(url, data=payload, allow_redirects=False)
    if resp is None:
        print("Failed to accept TOS")
        return False
    if not "tos_accepted" in resp.cookies:
        print("Failed to accept TOS, no tos_accepted cookie found.")
        return False
    print("TOS accepted, cookies updated.")
    with open(os.path.join(current_dir, "persistant_cookies.json"), "w") as f:
        json.dump(session.cookies.get_dict(), f, indent=4)
    return True


def get_tag_details(tag):
    tag = restore_tag(tag)
    url = f"https://e621.net/artists/show_or_new?name={tag}"
    resp = get(url)
    soup = Soup(resp.text, "html.parser")
    meta = {"version": "e6dl-1.0"}
    meta["main_tag"] = tag
    meta["description"] = ""
    artist_info = soup.find(id="c-artists")
    # remove .recent-posts
    if artist_info:
        for recent_posts in artist_info.find_all(class_="recent-posts"):
            recent_posts.decompose()
        h1_tag = artist_info.find("h1")
        if h1_tag:
            # meta["description"] += h1_tag.text.strip() + "<br>"
            h1_tag.decompose()
        for a in artist_info.find_all("a", href=True):
            if a["href"].startswith("/"):
                a["href"] = ""
    meta["description"] += str(artist_info)
    return meta


def download_tag_details(main_tag):
    tag_dir = os.path.join(args.output, main_tag)
    os.makedirs(tag_dir, exist_ok=True)
    if not os.path.exists(os.path.join(tag_dir, f"profile.json")):
        print(f"Downloading tag details for main tag '{main_tag}'...")
        tag_meta = get_tag_details(main_tag)
        with open(os.path.join(tag_dir, f"profile.json"), "w") as f:
            json.dump(tag_meta, f)


downloaded_posts = set()


def scan_downloaded_posts(main_tag):
    tag_dir = os.path.join(args.output, main_tag)
    if not os.path.exists(tag_dir):
        return
    for filename in os.listdir(tag_dir):
        if re.match(r"\d+_", filename):
            post_id = filename.split("_")[0]
            if os.path.exists(
                os.path.join(tag_dir, f"{post_id}.json")
            ):  # both file and json must exist to consider the post as downloaded
                downloaded_posts.add(post_id)
    print(
        f"Scanned downloaded posts for main tag '{main_tag}', found {len(downloaded_posts)} posts."
    )


def download_post(post_id, main_tag):
    print(f"Downloading post {post_id} with main tag '{main_tag}'...")
    url = f"https://e621.net/posts/{post_id}"
    resp = get(url)
    soup = Soup(resp.text, "html.parser")
    if not main_tag:
        print("No main tag provided, guessing main tag from post...")
        author_tags = soup.find_all(class_="tag-artist")
        author_tags = [
            tag["data-name"].strip().replace(" ", "_") for tag in author_tags
        ]
        author_tags = [
            unquote(tag) for tag in author_tags if tag not in artists_tags_exclude and tag
        ]
        print(f"Author tags found: {author_tags}")
        if len(author_tags) == 0:
            if args.no_interactive:
                print(
                    "No artist tag found, since --no-interactive is set, no way to choose main tag, skipping."
                )
                return
            while not args.main_tag:
                main_tag = input(
                    "No artist tag found, please enter a main tag to use for this post: "
                ).strip()
        else:
            main_tag = sanitize_tag(author_tags[0])
            print(
                f"Guessed main tag '{main_tag}' from post's author tags: {author_tags}"
            )
    post_dir = os.path.join(args.output, main_tag)
    download_tag_details(main_tag)
    meta = {"version": "e6dl-1.0"}
    meta["post_id"] = post_id
    meta["url"] = url
    meta["main_tag"] = main_tag
    tags_of_post = soup.find_all(class_="tag-list-item")
    tags_of_post = [unquote(tag["data-name"]).strip().replace(" ", "_") for tag in tags_of_post]
    meta["tags"] = tags_of_post
    description = soup.find(id="description")
    if description:
        description = str(description.find(class_="styled-dtext"))
    else:
        description = ""
    meta["description"] = description
    source_links = soup.find_all(class_="source-link")
    source_links = [link.a.attrs["href"] for link in source_links if link.a]
    meta["source_links"] = source_links
    try:
        file_url = soup.find("a", class_="ptbr-etc-download")["href"]
        meta["file_url"] = file_url
    except Exception as e:
        print(f"Failed to find file URL for post {post_id}: {e}")
        print(
            "File might be deleted or unavailable, skipping download but saving metadata."
        )
        file_url = ""
        meta["file_url"] = ""
    post_score = soup.find(class_="post-score").text.strip()
    meta["score"] = post_score
    faves = soup.find(id="sidebar-favcount").text.strip()
    meta["favorites"] = faves
    uploadDate = soup.find("meta", {"itemprop": "uploadDate"})["content"]
    # convert uploadDate to unix timestamp, eg. 2025-01-14T10:42:15-05:00
    upload_timestamp = int(
        time.mktime(time.strptime(uploadDate, "%Y-%m-%dT%H:%M:%S%z"))
    )
    meta["upload_time"] = upload_timestamp
    parent_div = soup.find(id="has-parent-relationship-preview")
    parents = []
    if parent_div:
        parents = parent_div.find_all("article")
        parents = [parent.attrs["data-id"] for parent in parents]
    meta["parents"] = parents
    meta["pools"] = {}
    for pool_nav in soup.find_all(class_="pool-nav"):
        pool_title = pool_nav.find(class_="nav-name").a.text.strip()
        pool_id = pool_nav.find(class_="nav-name").a["href"].split("/")[-1]
        meta["pools"][pool_id] = {"title": pool_title}

    os.makedirs(post_dir, exist_ok=True)
    fn = f"{post_id}_{file_url.split('/')[-1]}"
    filepath = os.path.join(post_dir, fn)
    if not file_url:
        print(f"No file URL found for post {post_id}, skipping file download.")
    elif os.path.exists(filepath):
        print(f"File {filepath} already exists, skipping download.")
    else:
        print(f"Downloading file from {file_url} to {filepath}...")
        try:
            file_resp = get(file_url)
            with open(filepath, "wb") as f:
                f.write(file_resp.content)
            print(f"File downloaded successfully.")
        except Exception as e:
            print(f"Failed to download file: {e}")

    with open(os.path.join(post_dir, f"{post_id}.json"), "w") as f:
        json.dump(meta, f)
    # delete legacy post file if exists
    legacy_filepath = os.path.join(post_dir, f"{fn}.json")
    if os.path.exists(legacy_filepath):
        print(f"Deleting legacy metadata file {legacy_filepath}...")
        os.remove(legacy_filepath)
    downloaded_posts.add(post_id)
    print(f"Post {post_id} downloaded and metadata saved.")


def download_search(tags, main_tag):
    scan_downloaded_posts(main_tag)
    download_tag_details(main_tag)
    print(f"Downloading search results for tags {tags} with main tag '{main_tag}'...")
    for p in range(1, 9999):
        print(f"Fetching page {p}...")
        url = f"https://e621.net/posts?tags={'+'.join(tags)}&page={p}"
        resp = get(url)
        soup = Soup(resp.text, "html.parser")
        posts = soup.find_all("article")
        if not posts:
            print("No more posts found, stopping.")
            break
        for post in posts:
            post_id = post.attrs["data-id"]
            if post_id in downloaded_posts:
                print(f"Post {post_id} already downloaded, skipping.")
                continue
            download_post(post_id, main_tag)


def main():
    if not accept_tos():
        print(
            "Failed to accept TOS, please contact the developer, or check if e621.net is down."
        )
        exit(1)
    url = args.url
    post_match = re.match(r"https?://e621\.net/posts/(\d+)", url)
    search_match = re.match(r"https?://e621\.net/posts\?tags=(.+)", url)
    if post_match:
        post_id = post_match.group(1)
        download_post(post_id, args.main_tag)
    elif search_match:
        tags = search_match.group(1)
        tags = tags.replace("+", " ")
        tags = tags.replace("%20", " ")
        tags = tags.strip().split()
        if len(tags) == 0:
            print("No tags found in search URL.")
            exit(1)
        elif len(tags) == 1:
            if not args.main_tag:
                args.main_tag = sanitize_tag(tags[0])
            download_search(tags, args.main_tag)
        else:
            if args.no_interactive and not args.main_tag:
                print(
                    "Multiple tags provided, since --no-interactive is set, no way to choose main tag, exiting."
                )
                exit(1)
            elif not args.main_tag:
                print("Multiple tags found in search URL:")
                for i, tag in enumerate(tags):
                    print(f"{i+1}. {tag}")
                choice = input("Enter the number of the main tag to download: ")
                try:
                    choice = int(choice)
                    if choice < 1 or choice > len(tags):
                        print("Invalid choice, exiting.")
                        exit(1)
                    args.main_tag = tags[choice - 1]
                except ValueError:
                    print("Invalid input, exiting.")
                    exit(1)
            elif args.main_tag not in tags:
                print(
                    f"Provided main tag '{args.main_tag}' not found in tags from URL, exiting."
                )
                exit(1)
            download_search(tags, args.main_tag)


if __name__ == "__main__":
    main()
