# -*- coding: utf-8 -*-
"""Scrub childish wording from scene descriptions in all word-list JSONs.
Keeps child-related scenes only for entries whose term IS a child/animal-baby word.
Creates .bak backups before rewriting.
"""
import json, glob, os, re, shutil

BASE = os.path.dirname(os.path.abspath(__file__))

# terms whose scene legitimately needs children/baby imagery
CHILD_TERMS = {"baby", "boy", "girl", "kid", "brother", "sister", "son", "daughter",
               "grandson", "孙子", "孩子", "小孩", "男孩", "女孩", "儿子", "女儿", "弟弟", "妹妹"}

SUBS = [
    (r"\bcute\s+", ""),
    (r"\badorable\s+", ""),
    (r"\bsmiling\s+", ""),
    (r"\bsmiley\s+", ""),
    (r"\bhappy\s+", ""),
    (r"\bcheerful\s+", ""),
    (r"\bfriendly\s+", ""),
    (r"\bplayful\s+", ""),
    (r"\bjoyful\s+", ""),
    (r"\bchubby\s+", ""),
    (r"\bcartoon\s+", ""),
    (r"\bfluffy\s+", ""),
    (r"\bfuzzy\s+", ""),
    (r"\bround\s+", ""),
    (r"\bcomfy\s+", "soft "),
    (r"\bcozy\s+", ""),
    (r"\bsoft glow", "glow"),
    (r"\bgolden (medal-winning )?character", "winner"),
    (r"\bmedal-winning character on a podium", "winner on a podium"),
    (r"\bsmiley print", "star print"),
    (r"\bbig smile", "calm expression"),
    (r"\bsmiling", ""),
]

PERSON_SUBS = [
    (r"\ba kid\b", "a person"),
    (r"\bthe kid\b", "the person"),
    (r"\bkids\b", "people"),
    (r"\ba child\b", "a person"),
    (r"\bchildren\b", "people"),
    (r"\ba little (hero|girl|boy)\b", r"a person"),
    (r"\ba small (hero|figure|person)\b", "a person"),
    (r"\blittle (brother|sister)\b", r"\1"),
    (r"\ba small figure\b", "a person"),
]

def clean_scene(scene, is_child_term):
    s = " " + scene + " "
    for pat, rep in SUBS:
        s = re.sub(pat, rep, s, flags=re.I)
    if not is_child_term:
        for pat, rep in PERSON_SUBS:
            s = re.sub(pat, rep, s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([,.;])", r"\1", s)
    s = s.strip()
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s

changed = 0
for path in sorted(glob.glob(os.path.join(BASE, "words_*.json"))):
    if path.endswith(".bak"):
        continue
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    shutil.copy(path, path + ".bak")
    for row in data["words"]:
        term = row[0].lower()
        is_child = term in CHILD_TERMS
        new = clean_scene(row[8], is_child)
        if new != row[8]:
            row[8] = new
            changed += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{os.path.basename(path)}: rewritten")

print("scenes changed:", changed)
