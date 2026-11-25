"""
comment_widget.py
-----------------
Step 6c: Server-side injection of comment widgets into rendered digest HTML.

For each <h3> in the rendered digest, look up its chain_id (by substring
matching canonical_title against h3 text), then attach data-chain-id and
inject a comment widget block immediately after the <h3>.

Pure stdlib (html.parser). No BeautifulSoup dependency added.
Returns the augmented HTML string.
"""

import html
import json
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIGESTS_DIR = PROJECT_ROOT / "data" / "digests"


# ── Title → chain_id index for one run ───────────────────────────────────────
def load_title_to_chain(run_id):
    """Return {canonical_title: chain_id} for primary + secondary stories
    in the given run. Empty dict if JSON missing, malformed, or has no
    chain_ids (pre-Step-6a digests)."""
    json_path = DIGESTS_DIR / f"{run_id}.json"
    if not json_path.exists():
        return {}
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    out = {}
    for story in data.get("stories", []):
        if story.get("tier") not in ("primary", "secondary"):
            continue
        title = story.get("canonical_title")
        chain_id = story.get("chain_id")
        if title and chain_id:
            out[title] = chain_id
    return out


# ── HTML walker: collect h3 spans ────────────────────────────────────────────
class _H3Collector(HTMLParser):
    """Walks rendered HTML once, building a list of (h3_open_pos,
    h3_close_pos, text_content) for every <h3> in the document.

    Positions index into the original HTML string by character offset.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h3_spans = []  # list of dicts: {open_start, open_end, close_start, close_end, text}
        self._depth = 0    # track nested tags inside h3 (probably never, but safe)
        self._cur = None

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            # getpos() returns (lineno, col); offset is what we want.
            start = self._tag_start_offset()
            end = start + len(self.get_starttag_text())
            self._cur = {
                "open_start": start,
                "open_end": end,
                "text_chunks": [],
                "close_start": None,
                "close_end": None,
            }
            self._depth = 1
        elif self._cur is not None:
            self._depth += 1

    def handle_endtag(self, tag):
        if self._cur is None:
            return
        if tag == "h3":
            start = self._tag_start_offset()
            end = start + len(f"</{tag}>")
            self._cur["close_start"] = start
            self._cur["close_end"] = end
            self.h3_spans.append({
                "open_start": self._cur["open_start"],
                "open_end": self._cur["open_end"],
                "close_start": self._cur["close_start"],
                "close_end": self._cur["close_end"],
                "text": "".join(self._cur["text_chunks"]).strip(),
            })
            self._cur = None
            self._depth = 0
        else:
            self._depth -= 1

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["text_chunks"].append(data)

    def _tag_start_offset(self):
        """Convert (lineno, offset_in_line) from getpos() to absolute char
        offset in the original HTML string. Requires `self._raw` set by caller."""
        line, col = self.getpos()
        # Find the start of `line`-th line in self._raw (1-indexed).
        idx = 0
        for _ in range(line - 1):
            nl = self._raw.find("\n", idx)
            if nl < 0:
                break
            idx = nl + 1
        return idx + col


def _find_h3_spans(html_str):
    parser = _H3Collector()
    parser._raw = html_str
    parser.feed(html_str)
    return parser.h3_spans


# ── Render comment widget HTML ───────────────────────────────────────────────
def _render_widget(chain_id, run_id, comments_for_chain, return_to):
    """Build the widget HTML block to inject after a matched <h3>.

    `comments_for_chain` is a list of comment dicts (from comments.list_for_chain).
    `run_id` is the digest run being viewed (used as created_run_id for new comments).
    `return_to` is the URL to redirect back to after add/edit/delete (with #anchor).
    """
    # All values that come from data are HTML-escaped before insertion.
    # `chain_id` is alphanumeric (chn_<hex>) but escape anyway as defense in depth.
    cid_esc = html.escape(chain_id, quote=True)
    rid_esc = html.escape(run_id, quote=True)
    return_to_esc = html.escape(return_to, quote=True)
    anchor = f"chain-{chain_id}"

    parts = []
    parts.append(f'<div class="comment-widget" id="{anchor}">')

    # Existing comments, newest edit first.
    if comments_for_chain:
        parts.append('<div class="comment-list">')
        for c in comments_for_chain:
            cmt_id_esc = html.escape(c["id"], quote=True)
            text_esc = html.escape(c["text"])
            edited_esc = html.escape(c.get("edited_at", ""))
            created_run_esc = html.escape(c.get("created_run_id", ""), quote=True)
            created_at_esc = html.escape(c.get("created_at", ""))

            # "Created" line: link to source digest only if different from current run.
            if c.get("created_run_id") and c.get("created_run_id") != run_id:
                created_html = (
                    f'<a href="/digest/{created_run_esc}#{anchor}">{created_at_esc}</a>'
                )
            else:
                created_html = created_at_esc

            parts.append('<div class="comment-item">')
            parts.append(f'<div class="comment-text">{text_esc}</div>')
            parts.append(
                f'<div class="comment-meta">'
                f'created {created_html}'
            )
            if c.get("edited_at") and c.get("edited_at") != c.get("created_at"):
                parts.append(f' &middot; edited {edited_esc}')
            parts.append('</div>')

            # Edit form (toggled via <details>; collapsed by default).
            parts.append('<details class="comment-edit"><summary>Edit</summary>')
            parts.append(f'<form method="POST" action="/comments/{cmt_id_esc}/edit">')
            parts.append(f'<input type="hidden" name="return_to" value="{return_to_esc}">')
            parts.append(f'<textarea name="text" required>{text_esc}</textarea>')
            parts.append('<button type="submit">Save</button>')
            parts.append('</form></details>')

            # Delete form (separate form; tiny inline button).
            parts.append(
                f'<form method="POST" action="/comments/{cmt_id_esc}/delete" '
                f'class="comment-delete" '
                f'onsubmit="return confirm(\'Delete this comment? This cannot be undone.\');">'
            )
            parts.append(f'<input type="hidden" name="return_to" value="{return_to_esc}">')
            parts.append('<button type="submit">Delete</button>')
            parts.append('</form>')

            parts.append('</div>')  # /comment-item
        parts.append('</div>')  # /comment-list

    # Add-new form (toggled via <details>).
    parts.append('<details class="comment-add"><summary>Add note</summary>')
    parts.append('<form method="POST" action="/comments/add">')
    parts.append(f'<input type="hidden" name="chain_id" value="{cid_esc}">')
    parts.append(f'<input type="hidden" name="created_run_id" value="{rid_esc}">')
    parts.append(f'<input type="hidden" name="return_to" value="{return_to_esc}">')
    parts.append('<textarea name="text" required placeholder="Editorial note, prediction, or follow-up…"></textarea>')
    parts.append('<button type="submit">Save note</button>')
    parts.append('</form></details>')

    parts.append('</div>')  # /comment-widget
    return "".join(parts)


# ── Public entrypoint: inject widgets into rendered digest HTML ──────────────
def inject_widgets(html_content, run_id, comments_module, return_to):
    """Mutate the rendered digest HTML to:
      1. add `data-chain-id="chn_xyz"` and `id="chain-chn_xyz"` to each
         matched <h3>
      2. inject a comment widget block immediately after each matched <h3>

    Stories without a chain_id (signal tier or pre-Step-6a digests) get
    no widget — page renders normally.

    `comments_module` is the comments.py module (passed in to keep this
    helper import-light and testable).
    """
    title_to_chain = load_title_to_chain(run_id)
    if not title_to_chain:
        return html_content  # nothing to do; old digest or no JSON

    spans = _find_h3_spans(html_content)
    if not spans:
        return html_content

    # Pre-fetch comments per chain (avoids re-reading comments.json inside loop).
    chain_to_comments = {}

    # Build a list of (insert_position, replacement, insert_after) edits,
    # then apply them in reverse order so positions stay valid.
    edits = []  # list of (start, end, replacement_text)

    # Sort canonical_titles longest-first so that if one is a substring of
    # another (rare but possible), the longer/more specific match wins.
    titles_sorted = sorted(title_to_chain.keys(), key=len, reverse=True)

    for span in spans:
        h3_text = span["text"]
        matched_title = None
        for title in titles_sorted:
            if title and title in h3_text:
                matched_title = title
                break
        if matched_title is None:
            continue

        chain_id = title_to_chain[matched_title]

        # Edit 1: rewrite the h3 opening tag to add data-chain-id + id.
        # We don't try to merge with existing attrs — markdown-it's <h3> output
        # is bare (`<h3>`), confirmed by visual inspection of digests.
        new_open = (
            f'<h3 data-chain-id="{html.escape(chain_id, quote=True)}" '
            f'id="chain-{html.escape(chain_id, quote=True)}-h3">'
        )
        edits.append((span["open_start"], span["open_end"], new_open))

        # Edit 2: inject widget immediately after </h3>.
        if chain_id not in chain_to_comments:
            chain_to_comments[chain_id] = comments_module.list_for_chain(chain_id)
        widget_html = _render_widget(
            chain_id,
            run_id,
            chain_to_comments[chain_id],
            return_to,
        )
        # Insert at close_end (zero-width edit: end == start, replacement = widget).
        edits.append((span["close_end"], span["close_end"], widget_html))

    # Apply edits right-to-left so earlier offsets remain valid.
    edits.sort(key=lambda e: e[0], reverse=True)
    out = html_content
    for start, end, replacement in edits:
        out = out[:start] + replacement + out[end:]
    return out
