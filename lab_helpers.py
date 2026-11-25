import os
import re
import io
import json
import contextlib
import subprocess
import urllib.request

MODEL_ID = "Qwen/Qwen3-4B"

EXERCISES = {
    1: ("password.py", "change/exercise1",
        "check_password must require at least 10 characters (instead of 8)."),
    2: ("email_check.py", "change/exercise2",
        "is_valid_email must require both '@' and '.' in the address."),
    3: ("vowels.py", "change/exercise3",
        "count_vowels must also count uppercase vowels."),
}

model = None
tokenizer = None
last_review = {}


def load_model():
    global model, tokenizer
    if model is not None:
        print("Model already loaded")
        return
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    print("Loading", MODEL_ID, "...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, torch_dtype=torch.float16, device_map="auto")
    print("[OK] Model loaded")


def ask(system, user, max_new_tokens=300):
    import torch
    if model is None:
        raise RuntimeError("Run load_model() first")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def skill(name="reviewer"):
    text = open(f"skills/{name}/SKILL.md", encoding="utf-8").read()
    text = re.sub(r"^---.*?---\s*", "", text, flags=re.S).strip()
    if not text or "WRITE YOUR" in text or "PASTE YOUR" in text:
        raise RuntimeError("Your skill is empty, write it first")
    return text


def get_diff(n):
    file, branch, _ = EXERCISES[n]
    if git("rev-parse", "--verify", "--quiet", branch) is None:
        raise RuntimeError(f"Branch {branch} doesn't exist yet")
    return git("diff", f"main...{branch}", "--", ".", ":(exclude)skills")


def check_pushed(n):
    file, branch, _ = EXERCISES[n]
    if git("status", "--porcelain", "--", file).strip():
        print(f"[WARNING] {file} has changes that are not committed")
    git("fetch", "--quiet", "origin")
    remote = git("rev-parse", f"origin/{branch}")
    if remote is None:
        print(f"[WARNING] {branch} is not on GitHub yet, push it first")
    elif remote != git("rev-parse", branch):
        print(f"[WARNING] you have commits that are not pushed")


# GitHub

def get_token():
    # same token as the clone, if it's in the remote url
    m = re.search(r"https://[^:/]+:([^@]+)@github\.com", git("remote", "get-url", "origin") or "")
    if m:
        return m.group(1)
    try:
        from google.colab import userdata
        return userdata.get("GH_TOKEN")
    except Exception:
        return os.environ.get("GH_TOKEN")


def github(method, path, data=None):
    token = get_token()
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(data).encode() if data else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "null")


def owner_repo():
    url = git("remote", "get-url", "origin").strip()
    return re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?$", url).groups()


def post_on_pr(n, text):
    owner, repo = owner_repo()
    branch = EXERCISES[n][1]
    prs = github("GET", f"/repos/{owner}/{repo}/pulls?state=open&head={owner}:{branch}")
    if not prs:
        print(f"[WARNING] No open PR for {branch}, open it on GitHub and run review again")
        return
    pr = prs[0]
    github("POST", f"/repos/{owner}/{repo}/issues/{pr['number']}/comments",
           {"body": f"**AI review, exercise {n}**\n\n```\n{text}\n```"})
    print("[OK] Posted on PR:", pr["html_url"])


def review(n, post=True):
    check_pushed(n)
    diff = get_diff(n)
    if not diff.strip():
        print("[WARNING] Nothing to review, you didn't commit anything on this branch")
        return
    user = f"Task: {EXERCISES[n][2]}\n\nDiff:\n```diff\n{diff}```"
    text = ask(skill(), user)
    print(text, "\n")
    last_review[n] = (text, state(n))
    if post:
        try:
            post_on_pr(n, text)
        except Exception as e:
            print("[WARNING] Couldn't post on GitHub:", e)


# checking

def run_quiet(f, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = f(*args)
    return result, buf.getvalue() != ""


def state(n):
    # "original", "changed" (the change from the notebook), "fixed" or "other"
    file, branch, _ = EXERCISES[n]
    ns = {}
    try:
        exec(git("show", f"{branch}:{file}"), ns)
        if n == 1:
            f = ns["check_password"]
            r8, p8 = run_quiet(f, "a" * 8)
            r9, p9 = run_quiet(f, "a" * 9)
            r10, p10 = run_quiet(f, "a" * 10)
            printed = p8 or p9 or p10
            if r8 and r10 and not printed:
                return "original"
            if not r8 and not r9 and r10:
                return "changed" if printed else "fixed"
        if n == 2:
            f = ns["is_valid_email"]
            key = (f("ana.upb.ro"), f("ana@upb"), f("ana@upb.ro"))
            return {(False, True, True): "original",
                    (True, True, True): "changed",
                    (False, False, True): "fixed"}.get(key, "other")
        if n == 3:
            f = ns["count_vowels"]
            if f("banana") == 3:
                return {0: "original", 2: "changed"}.get(f("ANA"), "other")
    except Exception:
        pass
    return "other"


def check_review(n):
    s = state(n)
    if s == "original":
        print("[FAIL] You didn't commit the change yet")
        return
    if s == "other":
        print(f"[FAIL] {EXERCISES[n][0]} doesn't look right, check your edit")
        return
    if n not in last_review:
        print(f"[FAIL] Run review({n}) first")
        return
    text, s_then = last_review[n]
    if s_then != s:
        print(f"[FAIL] The code changed since the last review, run review({n}) again")
        return

    v = re.search(r"Verdict\s*:\s*\**\s*(APPROVE|REQUEST\s+CHANGES)", text, re.I)
    p = re.search(r"Problem\s*:\s*(.*)", text, re.I | re.S)
    verdict = re.sub(r"\s+", " ", v.group(1).upper()) if v else None
    problem = p.group(1) if p else ""

    bad = s == "changed" and n in (1, 2)
    expected = "REQUEST CHANGES" if bad else "APPROVE"
    keywords = {1: [r"print"], 2: [r"\bor\b", r"\band\b", r"\bboth\b"]}.get(n, [])

    if verdict is None:
        print("[FAIL] No 'Verdict:' line in the review, check the format in your skill")
    elif verdict != expected:
        print(f"[FAIL] Verdict is {verdict}, but this PR should get {expected}")
    elif bad and not any(re.search(k, problem, re.I) for k in keywords):
        print("[FAIL] Right verdict, but the Problem line doesn't say what's actually wrong")
    elif bad:
        print(f"[OK] Your skill found the problem. Fix it, commit, push, run review({n}) again")
    else:
        print("[OK] APPROVE is right, you can merge the PR")


# release notes (bonus)

def release_notes(version="v1.0"):
    owner, repo = owner_repo()
    git("fetch", "--quiet", "origin")
    prs = github("GET", f"/repos/{owner}/{repo}/pulls?state=closed&base=main&per_page=50")
    prs = [p for p in prs if p.get("merged_at") and not p["head"]["ref"].startswith("release/")]
    if not prs:
        print("[WARNING] No merged PRs yet, merge your 3 PRs first")
        return
    prs.sort(key=lambda p: p["merged_at"])
    parts = []
    for p in prs:
        sha = p["merge_commit_sha"]
        diff = git("diff", f"{sha}^1", sha, "--", ".", ":(exclude)skills") or ""
        parts.append(f"PR #{p['number']}: {p['title']}\n{p['body'] or ''}\n"
                     f"Diff:\n```diff\n{diff}```")
    user = f"Version: {version}\n\nMerged Pull Requests:\n\n" + "\n\n".join(parts)
    text = ask(skill("release-notes"), user, max_new_tokens=500)
    open("CHANGELOG.md", "w", encoding="utf-8").write(text + "\n")
    print(text, "\n")
    print("[OK] Saved in CHANGELOG.md. Read it and fix it by hand before you commit.")


def publish_release(version="v1.0"):
    owner, repo = owner_repo()
    git("fetch", "--quiet", "origin")
    notes = git("show", "origin/main:CHANGELOG.md")
    if not notes:
        print("[FAIL] CHANGELOG.md is not in main yet, merge your release PR first")
        return
    try:
        r = github("POST", f"/repos/{owner}/{repo}/releases",
                   {"tag_name": version, "target_commitish": "main",
                    "name": version, "body": notes})
        print("[OK] Release published:", r["html_url"])
    except Exception as e:
        print(f"[FAIL] Couldn't publish {version} (maybe it already exists?):", e)
