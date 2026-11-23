"""
Gainsight Community thread export (FeverBee "AI Ready Communities")
===================================================================

Exports QUESTION threads and their replies from a Gainsight Customer
Communities (formerly inSided) community into two CSVs, INCLUDING the post
body text (which the out-of-the-box Gainsight exports leave out):

  1. topics.csv  - one row per thread  (title, opening post, category, views, date, ...)
  2. replies.csv - one row per reply   (thread, reply text, author, date,
                                        accepted answer?, written by staff?)

Only question topics in the categories listed in CATEGORIES are exported.
Conversations, articles, ideas and product updates are skipped.

Full step-by-step instructions (including installing Python) are in README.md.

QUICK START (Windows PowerShell)
--------------------------------
  python -m pip install requests
  $env:COMMUNITY_CLIENT_ID     = "<client id>"       # Control -> Integrations -> API
  $env:COMMUNITY_CLIENT_SECRET = "<client secret>"
  python export_community_threads.py

  * Edit the SETTINGS section below first.
  * The run is RESUMABLE. Progress is saved in ./export_cache/. If it stops
    (network drop, laptop sleep), just run it again and it carries on.
    Delete ./export_cache/ to start completely fresh.
  * SSL certificate errors usually mean a company security proxy is
    intercepting HTTPS - see the README troubleshooting section.

API facts this relies on:
  * Token lasts 2 hours -> we refresh it automatically.
  * /categories/{id}/topics has no total count -> stop when a page is short.
  * /categories/{id}/topics returns at most 10,000 results per query ->
    we fetch in date windows and split busy windows automatically.
  * Replies come oldest first with NO best-answer flag -> the accepted
    reply id comes from GET /questions/{id} -> "answer".
  * Replies' _metadata.totalCount only counts top-level replies -> never page on it.
  * User API needs "Accept: application/json" or it returns HTTP 500.
"""

import csv
import json
import os
import sys
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock

import requests

# =============================================================================
# SETTINGS - change these
# =============================================================================

# Your API host depends on where your community is hosted:
#   Europe: https://api2-eu-west-1.insided.com
#   US:     https://api2-us-west-2.insided.com
# Wrong host -> "invalid_client" error (looks like bad credentials).
API_HOST = "https://api2-eu-west-1.insided.com"

# Your community's web address, no slash at the end. Only used to build
# the thread links in topics.csv.
COMMUNITY_URL = "https://community.example.com"

# Only threads CREATED in this window. None = no limit.
CREATED_FROM = None             # "yyyy-mm-dd" or None (= all time)
CREATED_TO = None               # "yyyy-mm-dd" or None (= up to today)

# Used when CREATED_FROM is None. Earlier than most communities' oldest thread;
# empty years cost one quick call per board, so erring early is cheap.
EARLIEST_DATE = "1995-01-01"

# Threads are fetched in date windows this long, to stay under the API's
# 10,000-results-per-query cap. Busy windows get split automatically.
WINDOW_DAYS = 365

# Community role(s) that mark staff, for the author_is_staff columns.
# Built-in roles look like "roles.moderator"; custom roles use the name
# shown in Control (e.g. "Employee"). Use [] to skip the staff flag.
STAFF_ROLE_NAMES = ["roles.moderator", "roles.administrator"]

# Parallel requests for the per-thread calls. 4 is polite. Drop to 1 if you
# see lots of HTTP 429 (rate limit) messages.
WORKERS = 4

OUTPUT_DIR = Path(".")
CACHE_DIR = Path("./export_cache")

# Q&A categories to export. category_id = the number at the end of the
# category's URL, e.g. https://community.example.com/billing-questions-42 -> 42
# The two names go into the "product" and "category" columns - use any labels.
# (category_id: (product or group name, category name))
CATEGORIES = {
    # Product A
    12:  ("Product A", "Getting Started"),
    15:  ("Product A", "Installation & Setup"),
    18:  ("Product A", "Troubleshooting"),
    # Product B
    31:  ("Product B", "General Questions"),
    34:  ("Product B", "Integrations & API"),
    # Account & billing
    42:  ("Account", "Billing Questions"),
    47:  ("Account", "Login & Access"),
}

PAGE_SIZE = 100   # documented max


# =============================================================================
# API CLIENT - token handling + retries in one place
# =============================================================================

class CommunityApi:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None
        self.token_expires_at = 0
        self.token_lock = Lock()   # threads share one token
        self.session = requests.Session()

    def _get_new_token(self):
        response = self.session.post(
            f"{API_HOST}/oauth2/token",   # domain root, NOT under /v2
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "read",
            },
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        self.token = body["access_token"]
        # Refresh 5 minutes early so a long page never hits an expired token
        self.token_expires_at = time.time() + int(body["expires_in"]) - 300

    def _current_token(self):
        with self.token_lock:
            if self.token is None or time.time() >= self.token_expires_at:
                self._get_new_token()
            return self.token

    def get(self, path, params=None):
        """GET with retries. Returns parsed JSON, or None for a 404."""
        for attempt in range(1, 7):
            headers = {
                "Authorization": f"Bearer {self._current_token()}",
                "Accept": "application/json",   # required by the User API
            }
            try:
                response = self.session.get(
                    f"{API_HOST}{path}", headers=headers, params=params, timeout=120
                )
            except requests.RequestException as error:
                wait = 5 * attempt
                print(f"   network error ({error}); retry in {wait}s")
                time.sleep(wait)
                continue

            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return None
            if response.status_code == 401:
                # Token died early - force a new one and retry
                with self.token_lock:
                    self.token = None
                continue
            if response.status_code == 429 or response.status_code >= 500:
                wait = int(response.headers.get("Retry-After", 5 * attempt))
                print(f"   HTTP {response.status_code} on {path}; retry in {wait}s")
                time.sleep(wait)
                continue

            # Anything else (400, 403, 422...) is a real problem - stop loudly
            raise RuntimeError(f"HTTP {response.status_code} on {path}: {response.text[:500]}")

        raise RuntimeError(f"Gave up after 6 attempts: {path}")


# =============================================================================
# HTML -> plain text
# =============================================================================

class _HtmlToText(HTMLParser):
    """Keeps paragraphs/line breaks, turns images into [image], keeps link URLs."""

    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
                  "pre", "blockquote", "figure", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.image_count = 0
        self.current_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.image_count += 1
            self.parts.append(" [image] ")
        elif tag == "a":
            self.current_link = attrs.get("href")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self.current_link:
            self.parts.append(f" ({self.current_link})")
            self.current_link = None
        elif tag in self.BLOCK_TAGS and tag != "br":   # <br /> fires start AND end
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html):
    """Returns (plain_text, image_count)."""
    if not html:
        return "", 0
    parser = _HtmlToText()
    parser.feed(html)
    text = "".join(parser.parts).replace("\u00a0", " ")
    # Tidy: trim each line, collapse runs of blank lines to one
    lines = [line.strip() for line in text.split("\n")]
    tidy_lines = []
    for line in lines:
        if line == "" and (not tidy_lines or tidy_lines[-1] == ""):
            continue
        tidy_lines.append(line)
    return "\n".join(tidy_lines).strip(), parser.image_count


# =============================================================================
# STEP 1 - staff list (everyone with a role in STAFF_ROLE_NAMES)
# =============================================================================

def load_staff_ids(api):
    if not STAFF_ROLE_NAMES:
        print("Step 1: no staff roles set - every post will show author_is_staff = No")
        return set()

    cache_file = CACHE_DIR / "staff_ids.json"
    if cache_file.exists():
        return set(json.loads(cache_file.read_text()))

    print("Step 1: loading users with the staff role(s)...")
    staff_ids = set()
    # One role at a time - simple, and a user with two staff roles is
    # only counted once because staff_ids is a set.
    for role_name in STAFF_ROLE_NAMES:
        page = 1
        while True:
            body = api.get("/user", params={
                "filter[roles.rolename][]": role_name,
                "_returnIterable": "true",   # array instead of object keyed by id
                "page": page,
                "pageSize": PAGE_SIZE,
            })
            users = (body or {}).get("users", [])
            for user in users:
                staff_ids.add(str(user["userid"]))
            print(f"   {role_name} page {page}: {len(staff_ids)} staff so far")
            if len(users) < PAGE_SIZE:
                break
            page += 1

    cache_file.write_text(json.dumps(sorted(staff_ids)))
    return staff_ids


# =============================================================================
# STEP 2 - topic lists per category
# =============================================================================

# The topics endpoint refuses anything past the first 10,000 results
# (HTTP 422 "Only the most recent 10000 items can be returned").
# So we ask for one date window at a time (a year by default), and if a
# window still has 10,000+ threads we cut it in half and try again.
MAX_RESULTS_PER_QUERY = 10_000
MAX_PAGES = MAX_RESULTS_PER_QUERY // PAGE_SIZE   # 100 pages of 100


class TooManyResults(Exception):
    """A date window has more threads than the API will hand back."""


def fetch_topics_in_window(api, category_id, window_start, window_end):
    """All threads in one category created between two dates (yyyy-mm-dd strings)."""
    topics = []
    page = 1
    while True:
        params = {
            "page": page,
            "pageSize": PAGE_SIZE,
            "sort": "createdAt",
            "createdAt[from]": window_start.isoformat(),
            "createdAt[to]": window_end.isoformat(),
        }
        body = api.get(f"/v2/categories/{category_id}/topics", params=params)
        rows = (body or {}).get("result", [])
        for row in rows:
            # voteSet/likeSet can be huge arrays of user ids - drop them
            row.pop("voteSet", None)
            row.pop("likeSet", None)
        topics.extend(rows)

        if len(rows) < PAGE_SIZE:
            return topics                # short page = end of this window
        if page == MAX_PAGES:
            raise TooManyResults()       # page 101 would be a 422
        page += 1


def fetch_topics_split_if_needed(api, category_id, window_start, window_end):
    """Fetch a window; if it's over the cap, split it in two (repeats as needed)."""
    try:
        return fetch_topics_in_window(api, category_id, window_start, window_end)
    except TooManyResults:
        days = (window_end - window_start).days
        if days <= 1:
            raise RuntimeError(
                f"Category {category_id} has 10,000+ threads created on "
                f"{window_start} - can't split a single day any further."
            )
        middle = window_start + timedelta(days=days // 2)
        print(f"   {window_start} -> {window_end} is over 10,000 threads; splitting at {middle}")
        # The two halves share the 'middle' day on purpose, so no day can be
        # missed whether the API treats from/to as inclusive or exclusive.
        # Duplicates are removed afterwards.
        first_half = fetch_topics_split_if_needed(api, category_id, window_start, middle)
        second_half = fetch_topics_split_if_needed(api, category_id, middle, window_end)
        return first_half + second_half


def load_topics_for_category(api, category_id):
    overall_start = date.fromisoformat(CREATED_FROM) if CREATED_FROM else date.fromisoformat(EARLIEST_DATE)
    # +1 day so today's threads are included even if 'to' is exclusive
    overall_end = date.fromisoformat(CREATED_TO) if CREATED_TO else date.today() + timedelta(days=1)

    # Date range is in the cache file name, so changing CREATED_FROM/TO
    # never reuses a list built for a different range.
    cache_file = CACHE_DIR / f"topics_{category_id}_{overall_start}_{overall_end}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    topics_by_id = {}   # keyed by id -> overlapping window edges can't create duplicates
    window_start = overall_start
    while window_start < overall_end:
        window_end = min(window_start + timedelta(days=WINDOW_DAYS), overall_end)
        for topic in fetch_topics_split_if_needed(api, category_id, window_start, window_end):
            topics_by_id[topic["id"]] = topic
        window_start = window_end        # next window starts on this window's last day

    topics = list(topics_by_id.values())
    cache_file.write_text(json.dumps(topics))
    return topics


# =============================================================================
# STEP 3 - per thread: accepted answer id + all replies
# =============================================================================

def load_thread_details(api, topic):
    """Returns {"accepted_reply_id": str|None, "replies": [...]} for one thread."""
    internal_id = topic["id"]

    accepted_reply_id = None
    if topic.get("bestAnswer"):
        question = api.get(f"/v2/questions/{internal_id}")
        if question and question.get("answer"):
            accepted_reply_id = str(question["answer"])

    replies = []
    if int(topic.get("inclusiveReplyCount") or topic.get("replyCount") or 0) > 0:
        page = 1
        while True:
            body = api.get(
                f"/v2/questions/{internal_id}/replies",
                params={"page": page, "pageSize": PAGE_SIZE},
            )
            rows = (body or {}).get("result", [])
            replies.extend(rows)
            if len(rows) < PAGE_SIZE:   # NOT _metadata.totalCount (top-level only)
                break
            page += 1

    return {"accepted_reply_id": accepted_reply_id, "replies": replies}


def load_all_thread_details(api, topics):
    """Fetches details for every thread, saving progress as it goes."""
    progress_file = CACHE_DIR / "thread_details.jsonl"
    done = {}
    if progress_file.exists():
        with progress_file.open(encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                done[record["topic_id"]] = record["details"]

    todo = [topic for topic in topics if topic["id"] not in done]
    print(f"Step 3: replies - {len(done)} threads already cached, {len(todo)} to fetch")

    write_lock = Lock()
    with progress_file.open("a", encoding="utf-8") as handle, \
            ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(load_thread_details, api, topic): topic for topic in todo}
        for count, future in enumerate(as_completed(futures), start=1):
            topic = futures[future]
            details = future.result()   # re-raises any error from the worker
            done[topic["id"]] = details
            with write_lock:
                handle.write(json.dumps({"topic_id": topic["id"], "details": details}) + "\n")
                handle.flush()
            if count % 100 == 0 or count == len(todo):
                print(f"   {count}/{len(todo)} threads")

    return done


# =============================================================================
# STEP 4 - write the CSVs
# =============================================================================

def topic_url(topic):
    # Needs publicId (the number in the thread URL), NOT the internal id
    return f"{COMMUNITY_URL}/topic/show?tid={topic['publicId']}"


TOPIC_COLUMNS = [
    "thread_id", "internal_id", "title", "opening_post", "opening_post_image_count",
    "product", "category_id", "category", "tags", "content_type",
    "status", "has_accepted_answer", "closed", "views", "reply_count",
    "likes", "author_username", "author_is_staff", "created_at",
    "last_activity_at", "url",
]

REPLY_COLUMNS = [
    "reply_id", "internal_reply_id", "thread_id", "thread_title", "product",
    "category", "reply_position", "reply_text", "reply_image_count",
    "author_username", "author_is_staff", "is_accepted_answer", "likes",
    "created_at", "url",
]


def write_csvs(topics, thread_details, staff_ids):
    topics_path = OUTPUT_DIR / "topics.csv"
    replies_path = OUTPUT_DIR / "replies.csv"
    topic_count = 0
    reply_count = 0

    # utf-8-sig = Excel opens accents/symbols correctly
    with topics_path.open("w", newline="", encoding="utf-8-sig") as topics_file, \
            replies_path.open("w", newline="", encoding="utf-8-sig") as replies_file:
        topic_writer = csv.DictWriter(topics_file, fieldnames=TOPIC_COLUMNS)
        reply_writer = csv.DictWriter(replies_file, fieldnames=REPLY_COLUMNS)
        topic_writer.writeheader()
        reply_writer.writeheader()

        for topic in topics:
            product, board = CATEGORIES[int(topic["categoryId"])]
            details = thread_details.get(topic["id"], {"accepted_reply_id": None, "replies": []})
            accepted_id = details["accepted_reply_id"]
            body_text, body_images = html_to_text(topic.get("content"))
            author = topic.get("author") or {}

            topic_writer.writerow({
                "thread_id": topic["publicId"],
                "internal_id": topic["id"],
                "title": topic.get("title", ""),
                "opening_post": body_text,
                "opening_post_image_count": body_images,
                "product": product,
                "category_id": topic["categoryId"],
                "category": board,
                "tags": "; ".join(topic.get("tags") or []),
                "content_type": topic.get("contentType", ""),
                "status": "Solved" if topic.get("bestAnswer") else "Unsolved",
                "has_accepted_answer": "Yes" if topic.get("bestAnswer") else "No",
                "closed": "Yes" if topic.get("closed") else "No",
                "views": topic.get("views", 0),
                "reply_count": topic.get("inclusiveVisibleReplyCount", topic.get("replyCount", 0)),
                "likes": topic.get("likes", 0),
                "author_username": author.get("username", ""),
                "author_is_staff": "Yes" if str(author.get("id")) in staff_ids else "No",
                "created_at": topic.get("createdAt", ""),
                "last_activity_at": topic.get("lastActivityAt", ""),
                "url": topic_url(topic),
            })
            topic_count += 1

            position = 0
            for reply in details["replies"]:
                # Skip anything a public visitor wouldn't see
                if reply.get("trashed") or reply.get("spam") or reply.get("pendingApproval"):
                    continue
                position += 1
                reply_text, reply_images = html_to_text(reply.get("content"))
                reply_author = reply.get("author") or {}
                reply_writer.writerow({
                    "reply_id": reply.get("publicReplyId", ""),
                    "internal_reply_id": reply["id"],
                    "thread_id": topic["publicId"],
                    "thread_title": topic.get("title", ""),
                    "product": product,
                    "category": board,
                    "reply_position": position,
                    "reply_text": reply_text,
                    "reply_image_count": reply_images,
                    "author_username": reply_author.get("username", ""),
                    "author_is_staff": "Yes" if str(reply_author.get("id")) in staff_ids else "No",
                    "is_accepted_answer": "Yes" if str(reply["id"]) == accepted_id else "No",
                    "likes": len(reply.get("likedBy") or []),
                    "created_at": reply.get("repliedAt", ""),
                    "url": reply.get("permalink", ""),
                })
                reply_count += 1

    print(f"\nDone: {topic_count} threads -> {topics_path.resolve()}")
    print(f"      {reply_count} replies -> {replies_path.resolve()}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    client_id = os.environ.get("COMMUNITY_CLIENT_ID")
    client_secret = os.environ.get("COMMUNITY_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set COMMUNITY_CLIENT_ID and COMMUNITY_CLIENT_SECRET first (see top of file).")

    CACHE_DIR.mkdir(exist_ok=True)
    api = CommunityApi(client_id, client_secret)

    staff_ids = load_staff_ids(api)
    print(f"   {len(staff_ids)} staff accounts")

    print("Step 2: listing threads per board...")
    all_topics = []
    for category_id, (product, board) in CATEGORIES.items():
        topics = load_topics_for_category(api, category_id)
        # Belt and braces: published, not trashed, question-type only
        topics = [
            t for t in topics
            if not t.get("trashed")
            and t.get("status", "published") == "published"
            and t.get("contentType") == "question"
        ]
        print(f"   {product} > {board} ({category_id}): {len(topics)} threads")
        all_topics.extend(topics)

    thread_details = load_all_thread_details(api, all_topics)

    print("Step 4: writing CSVs...")
    write_csvs(all_topics, thread_details, staff_ids)


if __name__ == "__main__":
    main()
