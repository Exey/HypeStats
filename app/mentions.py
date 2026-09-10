"""Mentions: person names found in post texts, reconciled into one canonical
identity per person across channels.

Unlike app.tags (an *external* .md file, only reloaded into the app, never
written by it), `mentions.md` is app-owned and live-edited from the Mentions
view (app.ui.compare.mentions_view) — MentionsStore both reads and writes it,
the same way app.folders/app.tags own their JSON files, just Markdown-shaped
so it stays human-readable/diffable. Table shape:

    | id | names | unclear links |

- **id** — a channel `@username` or a full name (ФИО), the canonical
  identity this row represents.
- **names** — every name-variant text extracted from post captions that's
  been linked to this id (nicknames, declined forms, etc.), comma-separated.
- **unclear links** — `t.me/...` links to posts where the name showed up in
  a form too ambiguous to confidently attach (comma-separated), left for a
  human to resolve later.

Extraction itself (extract_person_names) is a thin wrapper around
mawo-slovnet's NewsNERTagger — a Russian NER model (PER/LOC/ORG spans).
Other things tried here, each ruled out for a concrete, verified reason
rather than assumed from docs/snippets:

- mawo-natasha — NamesExtractor is an unconditional stub, and its own NER
  path points at a dead model-download URL.
- DeepPavlov — its latest release pins numpy<1.24, which has no Python 3.12
  wheels (a failed install, not just its docs, which separately claim
  support only through 3.10/3.11 anyway).
- transformers running Babelscape/wikineural-multilingual-ner — genuinely
  caught more real names (e.g. "Марина", "Мария Рязанская", both missed by
  mawo-slovnet) but at the cost of a ~640MB model plus a torch dependency
  that made a packaged build balloon from ~90MB to ~726MB — not a trade
  worth it here, see find_known_names_in_text below for a lighter partial
  remedy instead.
- genuine natasha's NamesExtractor (rule-based, not the same as its NER
  path) — badly over-triggers on ordinary vocabulary: on the exact test
  sentences used to evaluate every option here, it tagged "и" (and),
  "без" (without), "Просто" (just) and a verb as name components. Its
  suggested "expand the dictionary" pipeline (CustomGrammemesPipeline,
  Combinator) doesn't exist in yargy or natasha's actual API either —
  checked directly against both packages' exports, not assumed.

Imported lazily and wrapped in a broad except so a channel without the
dependency installed, or a first-run model download that fails, degrades
to "no names found" instead of crashing the view.

find_known_names_in_text is the "lighter partial remedy" mentioned above: a
plain-Python supplemental pass, no model and no new dependency, that
catches a post mentioning someone already in mentions.md even when the NER
model doesn't tag that mention as PER at all (its most common failure
mode — a bare first name in a short, casual sentence). It doesn't help
with a person NOT already in mentions.md; extract_person_names is still
what finds those in the first place.

extract_person_names_by_pattern is the remaining gap those two leave: a
brand-new person, credited in a caption too terse or too oddly-shaped for
NER to tag as PER at all and not yet in mentions.md for the dictionary
scan to confirm either — e.g. "Модель: Алиса", "серия с Юлей", "Алиса, г.
Москва", "Алиса @alisa_channel". A third, purely positional pass (regex,
no model): a table of caption shapes these channels actually use (see its
own _PATTERN_SPECS), each scored by how strong a credit signal that shape
is, with a name candidate only surfaced above a tunable confidence floor.
It's deliberately permissive about what counts as a "name" (any
capitalized word) — what keeps that safe is that every hit still lands in
the Mentions view's Names Found table for the same human Link…/Ignore
review a NER hit gets, never written to mentions.md on its own.

NameExceptions is the opposite direction — a blocklist (name_exceptions.txt,
alongside mentions.md, plain text, one entry per line) of things
mawo-slovnet or find_known_names_in_text found that plainly aren't a
person, e.g. "Мастер-класс" ("master class", a compound noun the NER model
has tagged as PER before). Declension-aware the same way
MentionsStore.find_row is (see _names_declension_match), so one entry
covers every grammatical form. Pre-seeded with entries found during
development; the Mentions view's Names Found table also offers an
**Ignore** action next to **Link…** to add to it directly, for whatever
the pre-seeded list doesn't already cover.

Case/declension normalization (_ru_lemma, used by MentionsStore.find_row's
third-priority match tier) is a thin wrapper around pymorphy3 — a real
morphological analyzer, picked over the hand-rolled case-ending-suffix list
this module used before it (still here as _ru_stem's fallback for when
pymorphy3 isn't installed): pymorphy3 correctly lemmatizes adjectival
surnames (e.g. "Рязанской" -> "рязанский") that a fixed suffix list can't,
and — unlike pymorphy2 — has no pkg_resources import, so it doesn't need
the setuptools<81 pin pymorphy2 (and genuine natasha, which uses pymorphy2
internally) would need on a modern setuptools.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import config_dir

logger = logging.getLogger(__name__)


def mentions_path() -> Path:
    return config_dir() / "mentions.md"


# --------------------------------------------------------------- md i/o
def _split_cell(cell: str) -> list[str]:
    """Comma-separated cell -> a clean, order-preserving, de-duplicated list."""
    seen: dict[str, None] = {}
    for part in cell.split(","):
        part = part.strip()
        if part:
            seen.setdefault(part, None)
    return list(seen)


def _join_cell(values: list[str]) -> str:
    return ", ".join(v.strip() for v in values if v.strip())


def parse_md(text: str) -> list[dict]:
    """[{"id","names","links"}, …] from a "| id | names | unclear links |"
    table — tolerant of extra whitespace, skips the header/divider rows and
    any data row with an empty id. Same forgiving shape as app.tags's
    parse_md_table."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c or "-") for c in cells):
            continue  # divider row
        rows.append(cells)
    if not rows:
        return []
    out = []
    for cells in rows[1:]:
        cells += [""] * (3 - len(cells))
        id_ = cells[0].strip()
        if not id_:
            continue
        out.append({"id": id_, "names": _split_cell(cells[1]), "links": _split_cell(cells[2])})
    return out


# Tokenizes on Unicode letters (hyphen kept inside a word, so "Мастер-класс"
# stays one token) — used everywhere two names get compared word-by-word,
# instead of plain .split(), specifically so a decorative emoji glued
# straight onto a word with no space ("Курилко🔥", a common casual-writing
# style) doesn't become part of that word and break what would otherwise be
# an exact match.
_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _word_substring_match(candidate: str, needle: str) -> bool:
    """Whether (already casefolded) `candidate`'s words appear as a
    contiguous run inside `needle`'s — e.g. "лина жу" inside "мастер-класс
    лина жу" (NER grabbing extra text around a real name). Matched at word
    boundaries, not raw character containment — "иван" must not match
    inside "иванов" just because it happens to be a substring of those
    characters; it's a different word (a surname), not extra text around
    "иван"."""
    cwords, nwords = _words(candidate), _words(needle)
    if not cwords or len(cwords) > len(nwords):
        return False
    return any(nwords[i:i + len(cwords)] == cwords
              for i in range(len(nwords) - len(cwords) + 1))


def render_md(rows: list[dict]) -> str:
    lines = ["| id | names | unclear links |", "| --- | --- | --- |"]
    for row in rows:
        id_ = row.get("id", "").replace("|", "\\|")
        names = _join_cell(row.get("names") or []).replace("|", "\\|")
        links = _join_cell(row.get("links") or []).replace("|", "\\|")
        lines.append(f"| {id_} | {names} | {links} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------- Russian declension
# Primary: pymorphy3, a real morphological analyzer (dictionary-backed,
# gender/declension-class aware — correctly lemmatizes an adjectival
# surname like "Рязанской" to "рязанский", which no fixed suffix list would
# get right). Picked over pymorphy2 (and genuine natasha, which uses
# pymorphy2 internally) because pymorphy2 imports pkg_resources, gone from
# setuptools>=81 — pymorphy3 doesn't need that pin. Lazy-imported and
# wrapped in a broad except, same shape as _get_ner_pipeline below, so a
# machine without it installed falls back to _ru_stem: a curated list of
# common personal-name case endings, good enough for e.g.
# "Алисой"/"Алисы"/"Алисе" -> "Алиса" but not adjectival surnames.
_RU_CASE_SUFFIXES = (
    "иями", "ями", "ами", "ой", "ей", "ою", "ею",
    "ом", "ем", "им", "ым", "ах", "ях",
    "а", "я", "ы", "и", "е", "у", "ю", "й", "ь",
)

_morph = None
_morph_failed = False


def _get_morph():
    global _morph, _morph_failed
    if _morph is not None or _morph_failed:
        return _morph
    try:
        from pymorphy3 import MorphAnalyzer
        _morph = MorphAnalyzer()
    except Exception:  # noqa: BLE001 - missing/broken dependency falls back to _ru_stem
        logger.warning("pymorphy3 unavailable; falling back to the case-ending "
                       "heuristic for declension matching", exc_info=True)
        _morph_failed = True
        _morph = None
    return _morph


def _ru_stem(word: str) -> str:
    """Best-effort case-ending strip for one (already casefolded) word —
    never below 2 characters, so a short word isn't stripped to nothing.
    Only reached when pymorphy3 isn't installed (see _ru_lemmas)."""
    for suf in _RU_CASE_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            return word[:-len(suf)]
    return word


_RU_LEMMA_MIN_SCORE = 0.1


def _ru_lemmas(word: str) -> set[str]:
    """Dictionary forms pymorphy3 considers plausible for (already
    casefolded) `word`, scored at least _RU_LEMMA_MIN_SCORE — a set, not
    just the top guess, because pymorphy3 ranks genuinely ambiguous forms
    as ties (e.g. "марину" is equally likely the dative of the rare masc.
    name "Марин" or the accusative of "Марина", 0.5/0.5 — both come back
    here). The score floor is what keeps this from reintroducing the
    "иван"/"иванов" false positive that word-substring matching had before
    it was fixed: pymorphy3 does have a real parse of "иванов" as a rare
    plural form of "иван" ("у нас пять Иванов"), just at ~1% likelihood
    against the ~96% surname reading, so it's excluded by the floor.
    {_ru_stem(word)} if pymorphy3 isn't installed or fails on this word."""
    morph = _get_morph()
    if morph is None:
        return {_ru_stem(word)}
    try:
        parses = morph.parse(word)
        lemmas = {p.normal_form for p in parses if p.score >= _RU_LEMMA_MIN_SCORE}
        return lemmas or {p.normal_form for p in parses} or {word}
    except Exception:  # noqa: BLE001 - a single bad word shouldn't break a match
        return {_ru_stem(word)}


def _names_declension_match(a: str, b: str) -> bool:
    """Whether (already casefolded) `a` and `b` are plausibly the same
    (possibly multi-word) name in different grammatical cases — e.g. "иван
    петров" vs "ивана петрова". Word-by-word, same word count required, each
    pair either identical or sharing at least one dictionary form (see
    _ru_lemmas). Deliberately conservative — real lemmas, not fuzzy prefix
    closeness — so e.g. "иван" (a first name) doesn't collide with "иванов"
    (a whole different word, a surname, not a case form of the first
    name)."""
    wa, wb = _words(a), _words(b)
    if not wa or len(wa) != len(wb):
        return False
    return all(
        x == y or (len(x) >= 3 and len(y) >= 3 and _ru_lemmas(x) & _ru_lemmas(y))
        for x, y in zip(wa, wb))


def names_match(a: str, b: str) -> bool:
    """Whether `a` and `b` (any casing) refer to the same span of text —
    exact (word-for-word, case-insensitive) or a plausible Russian case
    variant of each other (see _names_declension_match). Used to match an
    extracted name against a link's anchor text from the same post (see
    channel_stat._extract_links and
    app.ui.compare.mentions_view._highlight_names_with_links) — NER might
    include a trailing emoji a link's anchor span doesn't, or the two might
    tag slightly different case forms of the same word, so this is a
    little more tolerant than a bare string comparison."""
    a, b = a.strip().casefold(), b.strip().casefold()
    if not a or not b:
        return False
    return _words(a) == _words(b) or _names_declension_match(a, b)


def is_telegram_link(url: str) -> bool:
    """Whether `url` is a t.me/telegram.me/telegram.org link -- the line
    every fair/fake/promo classification in this module (see
    classify_channel_links) and the Mentions view's own stats draw between
    "a real Telegram account" and "at best a web page mentioning someone"."""
    host = urlparse(url).netloc.lower()
    return host in ("t.me", "telegram.me", "telegram.org") or host.endswith(".t.me")


def tg_deep_link(url: str) -> str:
    """`url` converted to its tg:// deep-link equivalent when it's a t.me
    link this can confidently translate -- every desktop Telegram client
    registers tg:// as its own URL scheme, so opening this instead of the
    plain t.me url launches straight into it rather than a browser tab
    that then has to hand off (or doesn't). Used everywhere this app opens
    an external link (see app.ui.widgets.open_external_link) so a t.me
    link opens in Telegram app-wide, not just in one view.

    `url` unchanged for anything else: a non-Telegram link (opens in the
    browser as always), a bare private t.me/c/<id> link with no message id
    to resolve, or a join/invite link -- tg:// join links need the invite
    hash pulled out differently, and it's not worth it for how rarely one
    ever shows up here."""
    if not is_telegram_link(url):
        return url
    parts = urlparse(url).path.strip("/").split("/")
    if not parts or not parts[0]:
        return url
    if parts[0] == "c" and len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
        return f"tg://privatepost?channel={parts[1]}&post={parts[2]}"
    if parts[0] == "c":
        return url
    if parts[0].startswith(("joinchat", "+")):
        return url
    if len(parts) >= 2 and parts[1].isdigit():
        return f"tg://resolve?domain={parts[0]}&post={parts[1]}"
    return f"tg://resolve?domain={parts[0]}"


def tg_identity_key(url: str) -> str | None:
    """The stable identity a Telegram `url` resolves to -- "@username" for
    a public profile/post link, or the bare internal channel id for a
    private t.me/c/<id>/<msg> link (mentions.md has no username to file a
    private channel under, so a row for one has that raw number sitting in
    its own "id" column instead -- see resolve_telegram_link). None for a
    link this can't be reduced to an identity at all (a join/invite link,
    say) or that isn't a Telegram link in the first place."""
    if not is_telegram_link(url):
        return None
    parts = urlparse(url).path.strip("/").split("/")
    if not parts or not parts[0]:
        return None
    if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    if parts[0].startswith(("joinchat", "+")):
        return None
    return f"@{parts[0]}"


def canonical_link_key(url: str) -> str:
    """Case-insensitive grouping key for `url` -- a Telegram username is
    itself case-insensitive (t.me/geekography and t.me/Geekography are the
    same channel), so two links that only differ by casing there would
    otherwise count as two distinct links throughout classify_channel_links
    and the Mentions view's own link stats (_link_balance_stats_full,
    _most_repeated_link_full). Non-Telegram links are left exactly as they
    are -- a web URL's path is generally case-sensitive, unlike a Telegram
    username."""
    return url.casefold() if is_telegram_link(url) else url


def tg_has_post_id(url: str) -> bool:
    """Whether Telegram `url` points at a specific post (a numeric second
    path segment) rather than a channel's bare root -- t.me/c/<id> always
    carries the internal channel id as its own first segment, so "a
    specific post" there needs a *third* segment instead. Used to tell a
    channel's own "subscribe to us" self-link (bare, no post) from a post
    it's genuinely referencing (see classify_channel_links' own-channel
    exclusion) -- both are otherwise the same Telegram link shape."""
    parts = urlparse(url).path.strip("/").split("/")
    if not parts or not parts[0]:
        return False
    if parts[0] == "c":
        return len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit()
    return len(parts) >= 2 and parts[1].isdigit()


# A row's "id" column typed as a bare t.me/telegram.me link, with or
# without a scheme -- see _normalize_row_identity.
_URL_LIKE_ID_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", re.IGNORECASE)
# A plausible bare Telegram username (Telegram's own rule: starts with a
# letter, 5-32 characters total) -- as opposed to a ФИО id like "Ирина
# Теличева", which should never get an "@" glued onto it.
_BARE_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def _normalize_row_identity(raw_id: str) -> str:
    """A mentions.md row's own "id" column, canonicalized to the same
    "@username" (or bare internal-id) shape tg_identity_key(url) returns —
    someone can type a row's id as "@geekography", "geekography" (no @),
    or a full "t.me/geekography" link and mean the same channel; without
    this only the exact "@username" spelling would ever match a link's own
    identity in resolve_telegram_link. A ФИО id (no "@", not a link, not a
    plausible bare username) passes through unchanged — it was never going
    to match a Telegram identity key anyway, so there's nothing to
    normalize."""
    raw_id = (raw_id or "").strip()
    if not raw_id:
        return ""
    if _URL_LIKE_ID_RE.match(raw_id):
        url = raw_id if "://" in raw_id else f"https://{raw_id}"
        return tg_identity_key(url) or raw_id
    if raw_id.startswith("@") or raw_id.lstrip("-").isdigit():
        return raw_id
    if _BARE_USERNAME_RE.match(raw_id):
        return f"@{raw_id}"
    return raw_id


def resolve_telegram_link(url: str, texts: list[str], store: MentionsStore) -> dict | None:
    """The mentions.md row (if any) already known for Telegram `url` --
    checked first by identity (tg_identity_key(url) against every row's
    own id, normalized -- see _normalize_row_identity -- since a row's id
    might be spelled "@user", "user", or a full t.me link; identity is the
    only thing that works for a private channel anyway, whose row has no
    username to go by, just its raw internal id), then by whether any of
    `texts` (a link's own anchor text, across every post it was seen in)
    names a row MentionsStore.find_row already recognizes.

    Deliberately NOT MentionsStore.find_row_by_link (an exact-url lookup
    against a row's "unclear links" column): that column is a
    human-curated pending-review scratch list (see MentionsStore's own
    docstring), never populated by the normal Link… resolution flow in
    app.ui.compare.mentions_view (attach_name only, never attach_link), so
    in practice it almost never has anything to match against -- using it
    here is what made "Fair mentions" read as permanently 0."""
    key = tg_identity_key(url)
    if key is not None:
        needle = key.casefold()
        for row in store.rows:
            if _normalize_row_identity(row.get("id") or "").casefold() == needle:
                return row
    for text in texts:
        row = store.find_row(text)
        if row is not None:
            return row
    return None


def normalize_links(raw_links) -> list[dict]:
    """[{"text","url"}, …] regardless of which shape `raw_links` (a row's
    stored "links" field) is in: the current one (see
    channel_stat._extract_links) or the flat url-string list it used to be
    before anchor text was captured — a checkpoint fetched before that
    change won't have the richer shape until it's re-fetched (see
    tools.mentions_refresh), and this is what lets code that reads `links`
    not care which one it's looking at."""
    out = []
    for link in raw_links or []:
        if isinstance(link, str) and link.strip():
            out.append({"text": link, "url": link})
        elif isinstance(link, dict) and link.get("url"):
            out.append({"text": link.get("text") or link["url"], "url": link["url"]})
    return out


def name_tg_links(names: list[str], links: list[dict]) -> dict[str, str]:
    """{name: a representative t.me url} for every name in `names` that
    matches (see names_match) a Telegram-domain link's anchor text among
    `links` (already normalize_links()-d). A name backed by an actual
    Telegram link this way is a much higher-confidence "this really is a
    person/channel" signal than NER/dictionary-scan text alone —
    app.ui.compare.mentions_view's green Link… button and the @username it
    pre-fills when creating a new mentions.md row are both built on this."""
    out: dict[str, str] = {}
    for link in links:
        if not is_telegram_link(link["url"]):
            continue
        for name in names:
            if name not in out and names_match(name, link["text"]):
                out[name] = link["url"]
    return out


def name_link_matches(names: list[str], links: list[dict]) -> list[tuple[str, dict]]:
    """Every (name, link) pairing among `names`/`links` (already
    normalize_links()-d) where the link's own anchor text names that
    person — the same signal name_tg_links uses for Telegram links,
    generalized to any host so app.ui.compare.mentions_view's fairness
    stats can also see the web-resource ("fake") case. Unlike
    name_tg_links this keeps every match, not just the first per name —
    needed for occurrence counting."""
    return [(name, link) for link in links for name in names
            if names_match(name, link["text"])]


# ------------------------------------------------------------------ store
class MentionsStore:
    """In-memory rows plus a dirty flag — the Mentions view edits this
    directly and calls save() (explicitly, or automatically on leaving the
    view); nothing here talks to Qt."""

    def __init__(self) -> None:
        self.path = mentions_path()
        self.rows: list[dict] = []
        self.dirty = False
        self.load()

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            self.rows = []
        else:
            self.rows = parse_md(text)
        self.dirty = False

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = render_md(self.rows)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.dirty = False

    # ------------------------------------------------------------- lookup
    def find_row(self, name: str) -> dict | None:
        """The row (if any) whose id or a names-variant case-insensitively
        matches `name` — exactly; failing that, contained in `name` as a
        whole-word run (see _word_substring_match — mawo-slovnet's NER
        sometimes grabs extra text around a real name, e.g. extracting
        "Мастер-класс Лина Жу" when mentions.md already has "Лина Жу");
        failing that, a plausible Russian case variant (see
        _names_declension_match — "Алиса" in mentions.md matches an
        extraction of "Алисой" or "Алисы") — in that priority order,
        most-confident match first. What the Mentions view's per-channel
        extracted-names table uses to show its found/linked indicator.
        Substring candidates under 4 characters are skipped so a short
        fragment can't spuriously match unrelated text."""
        needle = name.strip().casefold()
        if not needle:
            return None
        for row in self.rows:
            if row.get("id", "").strip().casefold() == needle:
                return row
            if any(n.strip().casefold() == needle for n in row.get("names") or []):
                return row
        for row in self.rows:
            candidates = [row.get("id", "")] + list(row.get("names") or [])
            for c in candidates:
                c = c.strip().casefold()
                if len(c) >= 4 and _word_substring_match(c, needle):
                    return row
        for row in self.rows:
            candidates = [row.get("id", "")] + list(row.get("names") or [])
            for c in candidates:
                c = c.strip().casefold()
                if c and _names_declension_match(c, needle):
                    return row
        return None

    def find_row_by_link(self, url: str) -> dict | None:
        """The row (if any) whose "unclear links" already contain `url`
        exactly. Used to color a post's link-bearing highlighted name in
        the Mentions view's texts table (see
        app.ui.compare.mentions_view._highlight_names_with_links): if the
        exact same link a name is hyperlinked to in this post is already
        sitting in some row's unclear links, that's a strong hint of which
        person this is even when the bare name text alone is ambiguous
        (e.g. a first name with no surname)."""
        url = (url or "").strip()
        if not url:
            return None
        for row in self.rows:
            if url in (row.get("links") or []):
                return row
        return None

    # ------------------------------------------------------------- edits
    def add_row(self, id_: str, names: list[str] | None = None,
               links: list[str] | None = None) -> dict:
        row = {"id": id_, "names": list(names or []), "links": list(links or [])}
        self.rows.append(row)
        self.dirty = True
        return row

    def attach_name(self, row: dict, name: str) -> None:
        name = name.strip()
        if name and name.casefold() not in (n.casefold() for n in row["names"]):
            row["names"].append(name)
            self.dirty = True

    def attach_link(self, row: dict, link: str) -> None:
        link = link.strip()
        if link and link not in row["links"]:
            row["links"].append(link)
            self.dirty = True

    def remove_row(self, row: dict) -> None:
        if row in self.rows:
            self.rows.remove(row)
            self.dirty = True


# -------------------------------------------------------------- extraction
_tagger = None
_tagger_failed = False


def _get_tagger():
    global _tagger, _tagger_failed
    if _tagger is not None or _tagger_failed:
        return _tagger
    try:
        from mawo_slovnet import NewsNERTagger
        _tagger = NewsNERTagger()
    except Exception:  # noqa: BLE001 - missing/broken dependency degrades to no names
        logger.warning("mawo-slovnet unavailable; name extraction disabled", exc_info=True)
        _tagger_failed = True
        _tagger = None
    return _tagger


def extraction_available() -> bool:
    return _get_tagger() is not None


def extract_person_names(text: str) -> list[str]:
    """Distinct PER-span substrings NewsNERTagger finds in `text`, in the
    order first seen — raw as tagged, not case- or morphology-normalized
    (that's what linking a variant into a MentionsStore row, or the
    declension-aware match tier in MentionsStore.find_row, is for). Empty
    list if extraction isn't available or the text is blank."""
    text = (text or "").strip()
    if not text:
        return []
    tagger = _get_tagger()
    if tagger is None:
        return []
    try:
        markup = tagger(text)
    except Exception:  # noqa: BLE001 - a single bad post shouldn't break a scan
        logger.warning("NER tagging failed for one post", exc_info=True)
        return []
    seen: dict[str, None] = {}
    for span in getattr(markup, "spans", []) or []:
        if getattr(span, "type", None) != "PER":
            continue
        name = text[span.start:span.stop].strip()
        if name:
            seen.setdefault(name, None)
    return list(seen)


# ------------------------------------------------ known-name dictionary scan
def find_known_names_in_text(text: str, known: list[str]) -> list[str]:
    """Every one of `known` (id/names candidates from mentions.md) that
    shows up in `text` as a contiguous word run — exactly, or as a
    plausible Russian case variant of every word in it (see
    _names_declension_match) — returned spelled the way `text` actually has
    it, not the way `known` does. A supplemental pass alongside NER
    extraction (extract_person_names): this doesn't need the model to have
    tagged the mention as PER at all, which covers NER's most common miss
    here — a bare first name in a short, casual sentence. It can only ever
    confirm a name mentions.md already knows about; extract_person_names is
    still what finds a person nobody's added yet."""
    words = _words(text)
    if not words:
        return []
    cwords = [w.casefold() for w in words]
    found: dict[str, None] = {}
    for name in known:
        needle = [w.casefold() for w in _words(name)]
        if not needle:
            continue
        n = len(needle)
        for i in range(len(cwords) - n + 1):
            window = cwords[i:i + n]
            if window == needle or all(
                    x == y or (len(x) >= 3 and len(y) >= 3 and _ru_lemmas(x) & _ru_lemmas(y))
                    for x, y in zip(window, needle)):
                found.setdefault(" ".join(words[i:i + n]), None)
                break
    return list(found)


# ------------------------------------------------- positional pattern scan
# A capitalized word (Cyrillic or Latin, optionally hyphenated — "Мария",
# "Jean-Paul") — the shape a bare first name takes in these captions, and
# NER's most common miss (see extract_person_names' docstring: it needs
# sentence context a caption this short often doesn't give it).
_PAT_NAME1 = r"[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Z][а-яёa-z]+)?"
# One or two such words — a first name, or a first+last (ФИО) pair, for
# patterns explicit enough that the fuller shape is worth capturing.
_PAT_NAME2 = rf"{_PAT_NAME1}(?:\s+{_PAT_NAME1})?"
_PAT_CITY = _PAT_NAME1  # same shape as a name -- there's no telling them
                        # apart syntactically, only context does that
_PAT_DATE_NUM = r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?"
_PAT_MONTH_RU = (r"январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йея]|июн[ьяе]|"
                r"июл[ьяе]|август[ае]?|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе]")
_PAT_MONTH_OR_DATE = rf"(?:{_PAT_DATE_NUM}|(?:\d{{1,2}}\s+)?(?:{_PAT_MONTH_RU}))"
_PAT_YEAR = r"(?:19|20)\d{2}"
_PAT_MMYY = r"\d{1,2}/\d{2}\b"
_PAT_EMOJI = r"[\U0001F300-\U0001FAFF☀-➿]"

# (regex with exactly one capturing group around the name candidate,
# confidence 0..1) — probability rules for the caption shapes a model/person
# credit typically takes in these channels, ordered roughly high to low
# confidence within each group. A pattern this permissive would be far too
# noisy to write straight into mentions.md on its own; what makes it safe
# is that nothing here ever is — every hit still lands in the Mentions
# view's "Names Found" table exactly like a NER hit does, with the same
# Link…/Ignore review a person makes the actual call on (see
# app.ui.compare.mentions_view._rebuild_column, which is what actually
# calls extract_person_names_by_pattern). A wrong guess costs one click on
# Ignore, not a corrupted mentions.md row.
_PATTERN_SPECS: list[tuple[str, float]] = [
    # ---- explicit credit labels -- a caption deliberately crediting who's
    # in the post, the strongest signal there is short of an actual link ----
    (rf"\b[Мм]одель\s*:?\s*({_PAT_NAME2})", 0.9),
    (rf"\bModel\s*:?\s*({_PAT_NAME2})", 0.9),
    (rf"\b[Mm]d\s*[:\-]?\s*({_PAT_NAME2})", 0.85),
    (rf"\b[Мм]астер-класс\s+({_PAT_NAME2})", 0.85),
    (rf"\bкадре\s*:?\s+({_PAT_NAME2})", 0.85),
    (rf"\bсерия\s+с\s+({_PAT_NAME2})", 0.85),
    (rf"({_PAT_NAME2})\s*@\w+", 0.85),  # captioning straight to its own @username
    # ---- softer credit-shaped wording ----
    (rf"\bсерия\s+({_PAT_NAME2})\b", 0.6),
    (rf"\bwith\s+({_PAT_NAME2})\b", 0.6),
    (rf"\bс\s+({_PAT_NAME2})\b", 0.55),
    (rf"({_PAT_NAME2})\s+(?:poses|Full)\b", 0.6),
    (rf"({_PAT_NAME2})\s+(?:Продолжение|Полный|Вся|Больше|Актриса)\b", 0.6),
    (rf"({_PAT_NAME2})\s+https?://\S+", 0.55),
    (rf"{_PAT_EMOJI}\s*({_PAT_NAME1}),\s*{_PAT_MMYY}", 0.6),
    (rf"({_PAT_NAME1})[.,]?\s*г\.\s*{_PAT_CITY}", 0.6),
    (rf"({_PAT_NAME1})[.,]?\s+{_PAT_CITY}[.,]?\s+{_PAT_MONTH_OR_DATE}", 0.55),
    (rf"({_PAT_NAME1})\s*-\s*{_PAT_YEAR}\b", 0.55),
    (rf"({_PAT_NAME1})\s*/\s*{_PAT_YEAR}\b", 0.55),
    # ---- generic trailing preposition/verb -- common words on their own,
    # only worth much alongside other signal (or a human's own judgment) ----
    (rf"({_PAT_NAME1})\s+(?:for|by|see|with|в)\b", 0.35),
    (rf"({_PAT_NAME1})\s+Аппарат\b", 0.35),
    (rf"({_PAT_NAME1})\s*[❤️🔥]", 0.35),
    (rf"({_PAT_NAME1})\s*\|", 0.35),
    (rf"({_PAT_NAME1})[.,]?\s+{_PAT_CITY}\b", 0.3),
    (rf"({_PAT_NAME1})\s+{_PAT_MONTH_OR_DATE}\b", 0.3),
    (rf"({_PAT_NAME1})\s+{_PAT_YEAR}\b", 0.3),
    (rf"{_PAT_YEAR}\s+({_PAT_NAME1})\b", 0.3),
    # a word ending in one of these common Russian adjective/participle
    # suffixes, immediately before a name (e.g. "звёздная Мария") -- the
    # suffix itself isn't captured, just used to anchor the name after it.
    (rf"\S*(?:ная|ка|ко|ли|ал)\s+({_PAT_NAME1})\b", 0.35),
]
_COMPILED_PATTERNS = [(re.compile(pattern), score) for pattern, score in _PATTERN_SPECS]

# Only a pattern hit at or above this confidence is surfaced by
# extract_person_names_by_pattern's default threshold — tune here rather
# than at every call site if the "low" tier (~0.3, the bare
# name-next-to-a-common-word/city/year shapes) turns out worth surfacing
# too, or the "medium" tier (~0.55-0.6) turns out too noisy on real data.
PATTERN_MIN_CONFIDENCE = 0.5


def pattern_name_candidates(text: str) -> list[tuple[str, float]]:
    """Every (candidate, confidence) pattern match in `text` — see
    _PATTERN_SPECS — deduplicated by the exact candidate text, keeping
    each one's highest-scoring match if more than one pattern caught it.
    Unfiltered by confidence; extract_person_names_by_pattern is the
    thresholded, plain-list-of-names entry point everything else should
    use — this is exposed mainly so a caller that wants to show/tune the
    actual scores (or lower the threshold for one channel) can."""
    text = (text or "").strip()
    if not text:
        return []
    best: dict[str, float] = {}
    for pattern, score in _COMPILED_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            if name and score > best.get(name, 0.0):
                best[name] = score
    return list(best.items())


def extract_person_names_by_pattern(
        text: str, min_confidence: float = PATTERN_MIN_CONFIDENCE) -> list[str]:
    """Person-name candidates found by *position* in `text` — a model
    credited as "Модель: Алиса", "серия с Алисой", "Алиса, г. Москва",
    "Алиса @alisa_channel", and the many similar caption shapes these
    channels actually use (see _PATTERN_SPECS) — rather than by NER
    (extract_person_names) or an exact/declined match against a name
    mentions.md already knows (find_known_names_in_text). This is the
    remaining gap those two leave: a brand-new person, credited in a
    caption too terse or too oddly-shaped for the NER model to tag as PER
    at all (its context window is basically the whole caption; "Алиса, г.
    Москва" alone rarely reads as a name to it) and not yet in mentions.md
    for the dictionary scan to confirm either.

    Every hit is still only ever a *candidate* — see _PATTERN_SPECS' own
    note on why a false positive here costs one Ignore click, not a
    corrupted row. `min_confidence` filters pattern_name_candidates' raw
    scored output; results are returned in first-seen order, matching
    extract_person_names/find_known_names_in_text's own shape so a caller
    can merge all three into one list the same way."""
    return [name for name, score in pattern_name_candidates(text) if score >= min_confidence]


def extract_all_names_per_post(
        posts: list[dict], store: MentionsStore,
        name_exceptions: "NameExceptions | None" = None) -> list[list[str]]:
    """Parallel to `posts` (checkpoint rows carrying "full_text"/"text"):
    post i's own extracted names, via all three passes together —
    extract_person_names (NER), find_known_names_in_text (a dictionary
    scan against mentions.md's own id/name candidates), then
    extract_person_names_by_pattern (caption-position rules) as the last
    resort — the exact same combined pipeline
    app.ui.compare.mentions_view._rebuild_column runs per post for its own
    Names Found table, factored out here so a headless batch job (see
    tools.mentions_export) can run identically over a channel's stored
    posts without a Qt view to drive it, rather than a second copy of this
    loop slowly drifting out of sync with the interactive one."""
    known_candidates = [n for row in store.rows
                        for n in ([row.get("id", "")] + list(row.get("names") or []))
                        if n.strip()]
    out: list[list[str]] = []
    for post in posts:
        text = (post.get("full_text") or post.get("text") or "").strip()
        if not text:
            out.append([])
            continue
        names = extract_person_names(text)
        for extra in find_known_names_in_text(text, known_candidates):
            if extra not in names:
                names.append(extra)
        for extra in extract_person_names_by_pattern(text):
            if extra not in names:
                names.append(extra)
        if name_exceptions is not None:
            names = name_exceptions.filter(names)
        out.append(names)
    return out


# ---------------------------------------------------------- name exceptions
def name_exceptions_path() -> Path:
    return config_dir() / "name_exceptions.txt"


# Known NER false positives found during development — always checked,
# even on a fresh install with no name_exceptions.txt of its own yet (see
# NameExceptions.contains). "Мастер-класс" ("master class") is a compound
# noun mawo-slovnet has tagged as PER before.
_DEFAULT_NAME_EXCEPTIONS = {
    "мастер-класс",
}


class NameExceptions:
    """A small, flat, declension-aware blocklist of text that should never
    be treated as a person name — the Mentions view's Names Found table
    offers an **Ignore** action (next to **Link…**) that adds to this
    directly. Plain text, one entry per line, not a Markdown table like
    mentions.md/tags.md — there's nothing here but the blocked text itself.
    Unlike MentionsStore, there's no separate Save step: an add() takes
    effect (and persists) immediately, the same way clicking Ignore should
    feel — there's no meaningful "unsaved" state for a one-directional
    blocklist to sit in."""

    def __init__(self) -> None:
        self.path = name_exceptions_path()
        self._items: set[str] = set()
        self.load()

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            self._items = set()
            return
        self._items = {line.strip().casefold() for line in text.splitlines() if line.strip()}

    def contains(self, name: str) -> bool:
        """Whether `name` matches an exception exactly or as a plausible
        Russian case variant (see _names_declension_match) — checked
        against both the built-in defaults and whatever's been added to
        name_exceptions.txt."""
        needle = name.strip().casefold()
        if not needle:
            return False
        all_items = _DEFAULT_NAME_EXCEPTIONS | self._items
        if needle in all_items:
            return True
        return any(_names_declension_match(item, needle) for item in all_items)

    def filter(self, names: list[str]) -> list[str]:
        return [n for n in names if not self.contains(n)]

    def add(self, name: str) -> None:
        name = name.strip()
        if not name or self.contains(name):
            return
        self._items.add(name.casefold())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = sorted(self._items)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ------------------------------------------------------- link classification
# A non-Telegram link seen on this many distinct posts or more reads as the
# channel's own recurring plug (a Boosty/Patreon subscribe link, a shop...)
# rather than a one-off mention of a person -- see classify_channel_links's
# "promo" status. Deliberately low: a genuine one-off mention practically
# never repeats verbatim across posts the way a standing bio/subscribe link
# does.
_PROMO_REPEAT_THRESHOLD = 2


def classify_channel_links(entries: list[dict], store: MentionsStore,
                           name_exceptions: NameExceptions | None = None,
                           own_channel_key: str | None = None
                           ) -> dict[str, dict]:
    """Full-history classification of every link in `entries` (see
    channel_stat.py's checkpoint field `all_links` -- one {"date", "links":
    [{"text","url"}, ...]} entry per *scanned* post, not just the
    checkpoint's scored top-N `rows` pool used everywhere else in
    app.ui.compare.mentions_view), keyed by canonical_link_key(url) (case
    folded for a Telegram link, since t.me/geekography and t.me/Geekography
    are the same channel and would otherwise count as two distinct links):

        {"status": "fair" | "unresolved" | "fake" | "promo",
         "text": <a representative anchor text>, "count": <occurrences>,
         "names": <every distinct anchor text that named someone -- "fake" only>,
         "post_ids": {anchor text: [every post id it was seen under], ...},
         "url": <the exact-case url variant seen most often, for display>}

    `own_channel_key` -- this channel's own tg_identity_key(url), from
    whatever link the checkpoint itself was fetched under -- lets a bare
    self-link (this channel linking to its own root, no post: a "subscribe
    to us" plug reusing the exact same shape as crediting a person by
    linking to their channel) be excluded entirely rather than misread as
    a mention. A self-link to one of its *own* specific posts (has a post
    id -- see tg_has_post_id) is kept and classified normally; only the
    bare, post-less form is presumptively just a plug. None (the default)
    disables this check, e.g. for a caller that hasn't looked up the
    channel's own identity.

    Two of the four statuses cost nothing beyond a mentions.md lookup:

    - "fair": a Telegram link that resolves (see resolve_telegram_link) to
      a mentions.md row already -- either by identity (a private
      t.me/c/<id> link's own internal id, or a public link's username,
      matching some row's own "id") or by one of its anchor texts naming a
      row MentionsStore.find_row already recognizes.
    - "unresolved": a Telegram link *not* yet in mentions.md -- a Telegram
      anchor inherently names a real account, so this needs no further
      check either; it's exactly what becomes "fair" once linked (see the
      Mentions view's "Unresolved fair links" popup).

    A non-Telegram link is checked per distinct anchor text, cheapest
    first: find_known_names_in_text (a plain dictionary scan against
    mentions.md's own id/name candidates, no model), then
    extract_person_names (NER) if that finds nothing, then
    extract_person_names_by_pattern (the same caption-position rules
    app.ui.compare.mentions_view's own third extraction pass uses) as the
    last resort -- and this runs for *every* anchor the url was ever seen
    under, not gated by how often the url itself repeats. That matters
    because a channel's own repeat
    plug link is often reused, post after post, to credit whichever person
    is actually featured that time (e.g. always boosty.to/<photographer>,
    captioned with a different model's name each post) -- gating the name
    check on repetition would read every one of those as "obviously not a
    mention" and miss every single name. So:

    - "fake": *any* of the url's anchor texts names someone (a real
      person, but only web-linked, not a Telegram identity).
    - "promo": no anchor text names anyone, but the url repeats across >=
      _PROMO_REPEAT_THRESHOLD distinct posts anyway -- a standing plug
      (Boosty/Patreon/shop) with a caption that's never actually a name
      ("Смотреть здесь", "Boosty" every time).
    - otherwise dropped from the result entirely -- not fair, fake,
      unresolved or promo, just not a mention of anyone (a one-off web
      link with some other non-name caption).

    This is still what makes classifying the *entire* scanned history
    affordable where running full extraction on every post's text
    wouldn't be: NER only ever runs on the channel's distinct link anchor
    texts -- typically a few dozen at most -- never on the hundreds or
    thousands of posts scanned to find them."""
    known_candidates = [n for row in store.rows
                        for n in ([row.get("id", "")] + list(row.get("names") or []))
                        if n.strip()]
    own_key_cf = own_channel_key.casefold() if own_channel_key else None
    agg: dict[str, dict] = {}
    for entry in entries:
        post_id = entry.get("id")
        for link in entry.get("links") or []:
            raw_url = link.get("url")
            if not raw_url:
                continue
            key = canonical_link_key(raw_url)
            a = agg.setdefault(key, {"texts": [], "post_ids": {}, "count": 0, "url_counts": {}})
            a["count"] += 1
            a["url_counts"][raw_url] = a["url_counts"].get(raw_url, 0) + 1
            text = link.get("text") or raw_url
            if text not in a["texts"]:
                a["texts"].append(text)
            # Every post seen under this exact anchor text (not just the
            # first) -- the Mentions view's Link report jumps straight to
            # the one post if there's only one, or offers a pick-list if
            # this exact name/link combination came from several (see
            # mentions_view._open_link_report/_open_link_report_sources).
            post_id_list = a["post_ids"].setdefault(text, [])
            if post_id not in post_id_list:
                post_id_list.append(post_id)

    out: dict[str, dict] = {}
    for key, agg_entry in agg.items():
        texts, count = agg_entry["texts"], agg_entry["count"]
        post_ids = agg_entry["post_ids"]
        # Whichever exact casing showed up most often is what gets shown --
        # ties broken by first-seen (dict insertion order).
        url = max(agg_entry["url_counts"].items(), key=lambda kv: kv[1])[0]
        rep_text = texts[0]
        if is_telegram_link(url):
            if (own_key_cf is not None and not tg_has_post_id(url)
                    and (tg_identity_key(url) or "").casefold() == own_key_cf):
                continue  # this channel linking to its own bare root -- a
                          # subscribe plug, not a mention of anyone
            row = resolve_telegram_link(url, texts, store)
            out[key] = {"status": "fair" if row is not None else "unresolved",
                       "text": rep_text, "count": count, "post_ids": post_ids, "url": url}
            continue
        # Checked per distinct anchor text, not gated by `count` up front --
        # a channel's own repeat plug link is often *also* reused, post
        # after post, to credit whichever person is actually featured that
        # time (e.g. always boosty.to/<photographer>, captioned with a
        # different model's name each post), so a high repeat count alone
        # can't rule out a real name; only the absence of one across every
        # anchor this url was ever seen under can. Every anchor that names
        # someone is kept (not just the first) -- one repeat plug link
        # credits as many different people as it was ever captioned with,
        # and "Fake mentions" counts each of those, not the one link.
        name_texts: list[str] = []
        for text in texts:
            names = find_known_names_in_text(text, known_candidates)
            if not names:
                names = extract_person_names(text)
                if name_exceptions is not None:
                    names = name_exceptions.filter(names)
            if not names:
                # Last resort, same as app.ui.compare.mentions_view's own
                # third pass over a post's full text: an anchor whose
                # display text is itself a longer caption-shaped phrase
                # ("Model Alice", "Алиса, г. Москва") rather than a bare
                # name — NER/the dictionary scan need PER-tagged or
                # already-known text respectively, neither of which a
                # brand-new person's odd-shaped anchor necessarily is.
                names = extract_person_names_by_pattern(text)
                if name_exceptions is not None:
                    names = name_exceptions.filter(names)
            if names:
                name_texts.append(text)
        if name_texts:
            out[key] = {"status": "fake", "text": name_texts[0], "names": name_texts,
                       "count": count, "post_ids": post_ids, "url": url}
        elif count >= _PROMO_REPEAT_THRESHOLD:
            out[key] = {"status": "promo", "text": rep_text, "count": count,
                       "post_ids": post_ids, "url": url}
        # else: no name found under any anchor, and not repeated enough to
        # read as a standing plug either -- dropped, not a mention.
    return out


# ---------------------------------------------------- fairness ("Ethics")
def apply_no_link_penalty(fairness_pct: int | None, no_link_count: int, fair_count: int) -> int | None:
    """The Mentions view's "Fairness" (Dashboard/export: "Ethics") score,
    halved when a channel's "No link mentions" (see
    extract_all_names_per_post -- a name found in the post text with no
    accompanying link at all) outnumber its "Fair" links (a Telegram link
    already resolved to a mentions.md row). Crediting far more people by
    bare, unlinked name than by an actual working link reads as much less
    fair in practice than the plain fair/(fair+fake) ratio alone would
    say, even when every link the channel *does* give is perfectly
    honest -- so 100% halves to 50%, 80% to 40%, and so on.

    Applied identically everywhere Fairness is computed, so the
    interactive Mentions view (app.ui.compare.mentions_view.
    _update_stats_table) and the headless cached value
    (compute_channel_mentions_cache, below) never disagree. `fairness_pct` of
    None (no fair-or-fake data to divide at all) passes through
    unchanged — there's nothing to penalize."""
    if fairness_pct is None or no_link_count <= fair_count:
        return fairness_pct
    return round(fairness_pct / 2)


def compute_channel_mentions_cache(data: dict, store: MentionsStore,
                                   name_exceptions: NameExceptions | None = None
                                   ) -> dict | None:
    """The Mentions view's own per-channel Summary cards
    (app.ui.compare.mentions_view._update_stats_table) — Fairness, Fair,
    Fake, No Link, Unique links, and the tg/web balance — computed
    headlessly over a checkpoint's *entire* stored history (no period
    scoping — there's no period picker here) rather than from the
    interactive view. Lets the Dashboard's Ethics card, a folder's MD
    export (see tools.mentions_export.run_fairness_calculate), and the
    Mentions view's own instant-first-paint cache (see
    cache_channel_mentions and mentions_view._show_cached_stats) all
    show/reuse a channel's summary without recomputing it from scratch.

    None if this checkpoint predates `all_links` entirely (nothing to
    classify at all). Otherwise:

        {"fairness_pct": int | None, "fair": int, "fake": int,
         "no_link": int, "unique": int, "tg_pct": int | None,
         "web_pct": int | None}

    fairness_pct is None (rather than the dict itself) exactly when there's
    no fair-or-fake data to divide — the same "—" case the interactive
    card shows; apply_no_link_penalty's halving rule is already applied to
    it. unique/tg_pct/web_pct are grouped case-insensitively for a
    Telegram link the same way mentions_view._link_balance_stats_full
    does (see canonical_link_key)."""
    entries = data.get("all_links")
    if entries is None:
        return None
    own_channel_key = tg_identity_key(data.get("link") or "")
    classes = classify_channel_links(entries, store, name_exceptions, own_channel_key)
    fair = sum(1 for c in classes.values() if c["status"] == "fair")
    fake = sum(len(c.get("names") or []) for c in classes.values() if c["status"] == "fake")
    total_ff = fair + fake
    fairness_pct = round(fair / total_ff * 100) if total_ff else None

    posts = data.get("rows") or []
    no_link_count = 0
    if posts:
        all_names = extract_all_names_per_post(posts, store, name_exceptions)
        name_hits: set[str] = set()
        linked_names: set[str] = set()
        for post, names in zip(posts, all_names):
            links = normalize_links(post.get("links"))
            name_hits.update(names)
            for name, _link in name_link_matches(names, links):
                linked_names.add(name)
        no_link_count = len(name_hits - linked_names)
    fairness_pct = apply_no_link_penalty(fairness_pct, no_link_count, fair)

    seen: dict[str, bool] = {}
    for entry in entries:
        for link in entry.get("links") or []:
            url = link.get("url")
            if not url:
                continue
            key = canonical_link_key(url)
            if key not in seen:
                seen[key] = is_telegram_link(url)
    unique = len(seen)
    tg = sum(1 for is_tg in seen.values() if is_tg)
    tg_pct = round(tg / unique * 100) if unique else None
    web_pct = round((unique - tg) / unique * 100) if unique else None

    return {
        "fairness_pct": fairness_pct, "fair": fair, "fake": fake,
        "no_link": no_link_count, "unique": unique,
        "tg_pct": tg_pct, "web_pct": web_pct,
    }


def cache_channel_mentions(data: dict, cache: dict) -> None:
    """Stamps `cache` (see compute_channel_mentions_cache) with the current
    time and stores it as `data["mentions_cache"]` — the one place every
    writer of this cache (mentions_view._cache_fairness, the Dashboard
    Ethics card's "Calculate" button, tools.mentions_export.
    run_fairness_calculate) goes through, so "when was this last
    calculated" is never forgotten by one of them. Does not itself save
    `data` to disk — the caller does that (it usually has other changes
    to persist in the same write)."""
    data["mentions_cache"] = {**cache, "calculated_at":
                              time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
