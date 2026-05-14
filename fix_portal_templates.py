"""
Fix all portal_*.html templates:
1. Replace font-family:'Nunito' / font-family:"Nunito" with var(--font-body)
2. Fix old light-mode badge colors to dark-compatible versions
3. Update portal-nav styles to match iOS/Notion theme
4. Ensure modal backdrops use blur
"""
import os, re

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

COLOR_REPLACEMENTS = [
    # Nunito font
    (r"font-family:'Nunito(?:\s+Sans)?',\s*sans-serif", "font-family:var(--font-body)"),
    (r'font-family:"Nunito(?:\s+Sans)?",\s*sans-serif', "font-family:var(--font-body)"),
    (r"font-family:\s*'Nunito'", "font-family:var(--font-body)"),
    # Light badge colors → dark-compatible
    (r"background:#d1fae5;color:#065f46", "background:rgba(52,211,153,0.18);color:#34d399"),
    (r"background:#fef3c7;color:#92400e", "background:rgba(245,158,11,0.18);color:#fbbf24"),
    (r"background:#fee2e2;color:#991b1b", "background:rgba(239,68,68,0.18);color:#fca5a5"),
    (r"background:#dbeafe;color:#1e40af", "background:rgba(124,111,224,0.18);color:var(--violet-soft)"),
    (r"background:#f3f4f6;color:var\(--muted\)", "background:rgba(255,255,255,0.07);color:var(--fg-4)"),
    # Old accent-mid teal color
    (r"color:var\(--accent-mid,#4fb3b3\)", "color:var(--violet-soft)"),
    # .pnav-btn:hover that references --dark (old)
    (r"\.pnav-btn:hover\{color:var\(--dark\)\}", ".pnav-btn:hover{color:var(--fg-1)}"),
    (r"\.pnav-btn\.active\{color:var\(--accent\);border-bottom-color:var\(--accent\)", ".pnav-btn.active{color:var(--violet-soft);border-bottom-color:var(--violet)"),
    # modal backdrop — add blur if missing
    (r"background:rgba\(0,0,0,\.45\);z-index:1000", "background:rgba(0,0,0,.6);backdrop-filter:blur(8px);z-index:1000"),
    (r"background:rgba\(0,0,0,\.5\);z-index:1000", "background:rgba(0,0,0,.6);backdrop-filter:blur(8px);z-index:1000"),
    # modal-box — add dark background
    (r"\.modal-box\{background:var\(--surface\);border-radius", ".modal-box{background:var(--surface2);border:1px solid var(--line-strong);border-radius"),
    # news-card hover shadow
    (r"box-shadow:0 3px 16px rgba\(0,0,0,\.08\)", "box-shadow:0 3px 16px rgba(124,111,224,.15)"),
    # news-card-title color
    (r"color:var\(--dark\);margin-bottom:6px", "color:var(--fg-1);margin-bottom:6px"),
    # modal-close hover
    (r"\.modal-close:hover\{color:var\(--dark\)\}", ".modal-close:hover{color:var(--fg-1)}"),
]

def fix_file(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()

    original = content
    for pattern, replacement in COLOR_REPLACEMENTS:
        content = re.sub(pattern, replacement, content)

    if content != original:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Fixed: {os.path.basename(path)}")
    else:
        print(f"No changes: {os.path.basename(path)}")

files = [f for f in os.listdir(TEMPLATES_DIR) if f.startswith("portal_") and f.endswith(".html")]
files.append("api_docs.html")

for fname in sorted(files):
    fpath = os.path.join(TEMPLATES_DIR, fname)
    if os.path.exists(fpath):
        fix_file(fpath)

print("\nDone.")
