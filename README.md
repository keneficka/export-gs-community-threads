# Gainsight Community Thread Export

A Python script that exports the questions and replies from a **Gainsight Customer Communities** (formerly inSided) community into two CSV files. **The post text is included.** The built-in Gainsight exports leave it out.

It was written for the FeverBee **AI Ready Communities** course, which asks for these two files:

| File | One row per | Columns |
|---|---|---|
| `topics.csv` | thread | thread ID, title, **opening post text**, product, category, tags, Solved/Unsolved, closed, views, reply count, likes, author, author is staff, created date, last activity date, URL |
| `replies.csv` | reply | reply ID, thread ID, thread title, product, category, position in thread, **reply text**, author, author is staff, **is accepted answer**, likes, date, URL |

You don't need to know Python to use it. Follow the steps below in order.

> **What it exports:** only **question** topics from the categories you list. Conversations (discussions), articles, ideas and product updates are skipped.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install Python](#2-install-python)
3. [Download the script](#3-download-the-script)
4. [Open a terminal in the script's folder](#4-open-a-terminal-in-the-scripts-folder)
5. [Install the one dependency](#5-install-the-one-dependency)
6. [Get your API credentials](#6-get-your-api-credentials)
7. [Edit the settings in the script](#7-edit-the-settings-in-the-script)
8. [Run the script](#8-run-the-script)
9. [Open the results](#9-open-the-results)
10. [Troubleshooting](#10-troubleshooting)
11. [How it works (optional reading)](#11-how-it-works-optional-reading)

---

## 1. What you need

- A Windows or Mac computer.
- **Admin access to your community's Control environment.** You need it to create API credentials (step 6). If you don't have it, ask your community admin for a **client ID** and **client secret** with **read** access.
- About 15 minutes for setup. The export itself usually takes minutes, depending on how much content you have.

---

## 2. Install Python

### Windows

1. Go to <https://www.python.org/downloads/> and click the yellow **Download Python 3.x.x** button.
2. Run the downloaded installer.
3. **IMPORTANT:** on the first screen, tick **"Add python.exe to PATH"** at the bottom. If you skip this, the commands below won't work.
4. Click **Install Now** and wait for it to finish.
5. Check it worked: open **PowerShell** (press the Windows key, type `powershell`, press Enter) and run:

   ```powershell
   python --version
   ```

   You should see something like `Python 3.13.1`.

   - If you see `'python' is not recognized...`, close PowerShell, open it again and retry. If it still fails, run the installer again, choose **Modify**, and make sure "Add Python to environment variables" is ticked.
   - If the **Microsoft Store** opens instead, try `py --version`. If that works, use `py` wherever this guide says `python`.

> Work laptop with no install rights? Ask IT to install Python 3, or install it from your company's software portal if you have one.

### Mac

1. Go to <https://www.python.org/downloads/> and click **Download Python 3.x.x**.
2. Open the downloaded `.pkg` file and click through the installer.
3. Check it worked: open **Terminal** (press Cmd+Space, type `terminal`, press Enter) and run:

   ```bash
   python3 --version
   ```

> **Mac users:** wherever this guide says `python`, type `python3`. Wherever it says `pip`, type `pip3`.

---

## 3. Download the script

1. On this GitHub page, click the file **`export_community_threads.py`**.
2. Click the **Download raw file** button (the down-arrow icon near the top right of the file view).
3. Create a new folder for the export, for example `Documents\Community Export`, and move the file into it.

The CSV files and a working folder called `export_cache` will be created in this same folder.

---

## 4. Open a terminal in the script's folder

All the commands below must run **inside the folder that contains the script**.

### Windows

1. Open the folder in File Explorer.
2. Click the address bar at the top (where the folder path is shown), type `powershell`, and press **Enter**.

A PowerShell window opens already pointed at that folder. Check that the script is there:

```powershell
dir
```

You should see `export_community_threads.py` in the list.

### Mac

1. Open **Terminal**.
2. Type `cd ` (with a space after it), drag the folder from Finder into the Terminal window, then press **Enter**.

Check that the script is there:

```bash
ls
```

---

## 5. Install the one dependency

The script uses a library called **requests**. It is *not* part of standard Python, so you need to install it once:

**Windows**

```powershell
python -m pip install requests
```

**Mac**

```bash
python3 -m pip install requests
```

It's fine if you see `Requirement already satisfied`.

---

## 6. Get your API credentials

1. Log in to your community's **Control** environment (the admin back end).
2. Go to **Integrations → API**.
3. Create new credentials, or use existing ones. Note down the **client ID** and **client secret**.
4. Work out your **API host**. It depends on the region your community is hosted in:

   | Region | API host |
   |---|---|
   | Europe | `https://api2-eu-west-1.insided.com` |
   | US | `https://api2-us-west-2.insided.com` |

   If you aren't sure, try one. If you get an `invalid_client` error (see [Troubleshooting](#10-troubleshooting)), switch to the other. Your Gainsight contact can also confirm it.

> 🔒 **Treat the client secret like a password.** Don't paste it into the script, email it, or commit it to GitHub. The script reads it from your terminal session instead (step 8). The script only ever *reads* data. It never changes anything in your community.

---

## 7. Edit the settings in the script

Open `export_community_threads.py` in a text editor. Notepad (Windows) or TextEdit (Mac) is fine; [VS Code](https://code.visualstudio.com/) is nicer. Everything you need to change is in the **SETTINGS** section near the top. Don't change anything below it.

### 7.1 API host and community URL

```python
API_HOST = "https://api2-eu-west-1.insided.com"   # from step 6
COMMUNITY_URL = "https://community.example.com"   # your community's address, no slash at the end
```

`COMMUNITY_URL` is only used to build the link to each thread in `topics.csv`.

### 7.2 Date range

```python
CREATED_FROM = None             # "yyyy-mm-dd" or None (= all time)
CREATED_TO = None               # "yyyy-mm-dd" or None (= up to today)
```

Examples:

- All time: leave both as `None`.
- Last two years: `CREATED_FROM = "2024-11-01"`.
- Just 2025: `CREATED_FROM = "2025-01-01"` and `CREATED_TO = "2025-12-31"`.

Keep the quote marks around dates. `None` has **no** quotes and a capital N.

### 7.3 Staff role(s)

The script marks posts written by staff (the `author_is_staff` column). It decides who counts as staff by **community role**:

```python
STAFF_ROLE_NAMES = ["roles.moderator", "roles.administrator"]
```

- Built-in roles look like `roles.moderator` and `roles.administrator`.
- Custom roles use the name exactly as it appears in Control, for example `"Employee"` or `"Support Team"`.
- To see your role names, go to **Control → Users → Roles**.
- To skip the staff flag, use an empty list: `STAFF_ROLE_NAMES = []`. Every post will then show `No`.

### 7.4 Categories

This is the list of categories (boards) to export. Each line has this format:

```python
    category_id: ("Product or group name", "Category name"),
```

- **`category_id`** is the number at the end of the category's address. For example, in `https://community.example.com/billing-questions-42` it's **42**.
- **The two names** go straight into the `product` and `category` columns of the CSVs. Use whatever labels help your analysis. They don't have to match the community exactly.

Example:

```python
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
```

Rules that trip people up:

- Every line ends with a **comma**.
- Keep the opening `{` and closing `}`.
- Lines starting with `#` are notes and are ignored, so use them to group your categories.
- Use straight quotes `"`, not curly quotes `“ ”`. (Word and some note apps change them automatically, so don't edit the script in Word.)

**Which categories to include:** for the course, pick your support-style Q&A categories. Leave out welcome and how-to areas, announcements, feedback, off-topic and private or internal categories.

Save the file when you're done.

---

## 8. Run the script

In the terminal from step 4, set your credentials first. These last only until you close the window, which keeps them out of the script.

**Windows (PowerShell)**

```powershell
$env:COMMUNITY_CLIENT_ID     = "paste-your-client-id-here"
$env:COMMUNITY_CLIENT_SECRET = "paste-your-client-secret-here"
python export_community_threads.py
```

**Mac (Terminal)**

```bash
export COMMUNITY_CLIENT_ID="paste-your-client-id-here"
export COMMUNITY_CLIENT_SECRET="paste-your-client-secret-here"
python3 export_community_threads.py
```

> Using the old **Command Prompt** (`cmd`) on Windows instead of PowerShell? Use `set COMMUNITY_CLIENT_ID=paste-here` (no quotes, no spaces around `=`).

You'll see progress like this:

```
Step 1: loading users with the staff role(s)...
   page 1: 87 staff so far
Step 2: listing threads per board...
   Product A > Getting Started (12): 1843 threads
   ...
Step 3: replies - 0 threads already cached, 9214 to fetch
   100/9214 threads
   200/9214 threads
   ...
Step 4: writing CSVs...

Done: 9214 threads -> C:\...\topics.csv
      38120 replies -> C:\...\replies.csv
```

### If it stops part-way

Just run the same command again. Progress is saved in the `export_cache` folder, so the script carries on where it stopped instead of starting over. If you closed the terminal, set the two credential lines again first.

### Starting fresh

Delete the `export_cache` folder, then run the script again. Do this if:

- you change the **categories** or **staff roles**, or
- your last run was a while ago and you want the latest replies.

(Changing only the date range is fine without deleting it.)

---

## 9. Open the results

`topics.csv` and `replies.csv` are created next to the script.

- **Excel:** double-clicking usually works. The files are UTF-8 with a BOM, so accented characters and symbols display correctly.
- **Long posts:** post text keeps its line breaks, so some rows will look tall in Excel. That's expected. Use *Home → Format → Row Height* to tidy the view.
- **Images:** images show up as `[image]` in the text, and a count column tells you how many each post had. Links keep their URL in brackets after the link text.
- **Joining the files:** `thread_id` is in both files, so you can use it to join them in Excel, Google Sheets, Python or an AI tool.

> ⚠️ The files contain member usernames and everything they posted. Handle them under your company's data and privacy rules before uploading them to any third-party AI tool.

---

## 10. Troubleshooting

| What you see | What to do |
|---|---|
| `'python' is not recognized` / `command not found` | Python isn't on your PATH. On Windows, re-run the installer (step 2) and tick **Add python.exe to PATH**, or try `py`. On Mac, use `python3`. |
| `ModuleNotFoundError: No module named 'requests'` | Run step 5 again. |
| `Set COMMUNITY_CLIENT_ID and COMMUNITY_CLIENT_SECRET first` | Run the two credential lines from step 8 in the **same** terminal window, then run the script. |
| `HTTP 401` or `invalid_client` | Wrong API host for your region (step 6), or a typo in the client ID or secret. Check for stray spaces. |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Usually a company network or security proxy (Zscaler, Netskope and similar) intercepting HTTPS. Try from a network without it, or ask IT how Python should trust the company certificate. |
| Lots of `HTTP 429 ... retry in Xs` messages | You're being rate-limited. The script waits and retries by itself. If it keeps happening, set `WORKERS = 1` in the settings. |
| `HTTP 500` on `/user` | Rare and usually temporary. The script retries. If it persists, set `STAFF_ROLE_NAMES = []` to skip the staff lookup. |
| A category shows `0 threads` | Check the ID against the category's address. The script only exports **questions**, so a category made of conversations or articles will show 0. |
| `SyntaxError` when starting | Usually a missing comma, a missing quote or curly quotes in the CATEGORIES list (step 7.4). The error message shows the line number. |
| `Category ... has 10,000+ threads created on <date>` | Extremely unlikely. It means more than 10,000 questions were posted in one category on a single day. |

---

## 11. How it works (optional reading)

It uses the **Gainsight Customer Communities REST API** with read-only, client-credential access.

1. It gets an access token. Tokens last 2 hours and are refreshed automatically.
2. It builds the staff list: every user with the role(s) in `STAFF_ROLE_NAMES`.
3. For each category, it lists every question thread. The API returns at most 10,000 results per query, so the script asks for one year at a time and automatically splits any busier period into smaller windows.
4. For each thread, it fetches all replies. If the thread is solved, it also fetches the thread itself to find which reply is the accepted answer, because the replies list doesn't say.
5. It converts the HTML of each post to plain text and writes the two CSVs.

Deleted, spam and pending replies are left out, as are deleted threads.

**Views** are lifetime totals. The API doesn't provide views for a time period such as the last 12 months.
