"""
Replace simplified portal nav in management pages with the full Notion-style nav
that includes dropdowns and click-toggle support.

Pages to update:
  portal_news.html     → active: 관리 > 공지
  portal_contacts.html → active: 관리 > 문의
  portal_audit.html    → active: 관리 > 감사로그
  portal_files.html    → active: 데이터 > 파일관리
  portal_participants.html → active: 데이터 > 전체참여자
  portal_sessions.html → active: 데이터 > 전체세션
  portal_merge.html    → active: 데이터 > 병합
  portal_settings.html → standalone settings link
  portal_wardy.html    → active: App
"""
import os, re

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Full portal nav CSS (goes inside each template's <style> block)
NAV_CSS = """/* Portal nav */
.portal-nav{background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:var(--nav-h,72px);z-index:50;}
.portal-nav-inner{display:flex;align-items:stretch;gap:0;padding:0 20px;overflow-x:auto;scrollbar-width:none;}
.portal-nav-inner::-webkit-scrollbar{display:none;}
.pnav-btn{font-family:var(--font-body);font-size:.82rem;font-weight:500;padding:11px 14px;color:var(--fg-3);border-bottom:2px solid transparent;transition:color .15s,border-color .15s;text-decoration:none;display:flex;align-items:center;gap:5px;white-space:nowrap;}
.pnav-btn:hover{color:var(--fg-1);}
.pnav-btn.active{color:var(--violet-soft);border-bottom-color:var(--violet);font-weight:600;}
.pnav-drop{position:relative;display:flex;align-items:stretch;}
.pnav-drop-btn{cursor:pointer;background:none;border:none;font-family:var(--font-body);font-size:.82rem;font-weight:500;padding:11px 14px;color:var(--fg-3);border-bottom:2px solid transparent;transition:color .15s;white-space:nowrap;display:flex;align-items:center;gap:5px;}
.pnav-drop-btn:hover,.pnav-drop.open .pnav-drop-btn{color:var(--fg-1);}
.pnav-drop-btn .arrow{font-size:.65rem;transition:transform .2s;}
.pnav-drop:hover .arrow,.pnav-drop.open .arrow{transform:rotate(180deg);}
.pnav-drop.active-section .pnav-drop-btn{color:var(--violet-soft);border-bottom:2px solid var(--violet);}
.pnav-drop-menu{display:none;position:absolute;top:100%;left:0;background:var(--surface2);border:1px solid var(--line-strong);border-radius:0 0 12px 12px;min-width:190px;box-shadow:0 12px 40px rgba(0,0,0,.5);backdrop-filter:blur(16px);z-index:200;}
.pnav-drop:hover .pnav-drop-menu,.pnav-drop.open .pnav-drop-menu{display:block;}
.pnav-drop-menu a{display:flex;align-items:center;gap:8px;padding:11px 16px;font-size:.82rem;font-family:var(--font-body);color:var(--fg-2);text-decoration:none;border-bottom:1px solid var(--border);white-space:nowrap;transition:color .1s,background .1s;}
.pnav-drop-menu a:last-child{border-bottom:none;}
.pnav-drop-menu a:hover,.pnav-drop-menu a.active{background:rgba(124,111,224,.12);color:var(--violet-soft);}"""

# Nav click-toggle JS
NAV_JS = """// Portal nav dropdown click toggle
document.querySelectorAll('.pnav-drop-btn').forEach(btn=>{
  btn.addEventListener('click',e=>{
    e.stopPropagation();
    const drop=btn.closest('.pnav-drop');
    const wasOpen=drop.classList.contains('open');
    document.querySelectorAll('.pnav-drop').forEach(d=>d.classList.remove('open'));
    if(!wasOpen)drop.classList.add('open');
  });
});
document.addEventListener('click',()=>document.querySelectorAll('.pnav-drop').forEach(d=>d.classList.remove('open')));"""

def make_nav(data_active="", mgmt_active="", proj_active=False, app_active=False, tasks_active=False):
    """Generate the full portal nav HTML."""
    proj_cls = " active" if proj_active else ""
    tasks_cls = " active" if tasks_active else ""
    app_cls = " active" if app_active else ""
    data_section = " active-section" if data_active else ""
    mgmt_section = " active-section" if mgmt_active else ""

    def a(url_name, label, active_key, display_label=None):
        cls = " class=\"active\"" if active_key == data_active or active_key == mgmt_active else ""
        display = display_label or label
        return f'          <a href="{{{{ url_for(\'{url_name}\') }}}}"{cls}>{display}</a>'

    html = f"""  <!-- ── 포털 네비게이션 ── -->
  <div class="portal-nav">
    <div class="container portal-nav-inner">

      <a href="{{{{ url_for('portal') }}}}" class="pnav-btn{proj_cls}">🗂 프로젝트</a>

      <div class="pnav-drop{data_section}">
        <button class="pnav-drop-btn">📊 데이터 <span class="arrow">▾</span></button>
        <div class="pnav-drop-menu">
{a('portal_all_participants','전체 참여자','participants')}
{a('portal_all_sessions','전체 세션','sessions')}
{a('portal_merge','프로젝트 병합','merge')}
{a('portal_files','파일 관리','files','📁 파일 관리')}
        </div>
      </div>

      <a href="{{{{ url_for('portal_tasks') }}}}" class="pnav-btn{tasks_cls}">📅 일정</a>

      <a href="{{{{ url_for('portal_wardy') }}}}" class="pnav-btn{app_cls}">📱 App</a>

      <div class="pnav-drop{mgmt_section}">
        <button class="pnav-drop-btn">⚙️ 관리 <span class="arrow">▾</span></button>
        <div class="pnav-drop-menu">
{a('portal_news','공지 관리','news','📣 공지 관리')}
{a('portal_contacts','문의 수신함','contacts','✉ 문의 수신함')}
{a('portal_settings','설정','settings')}
{a('portal_audit','감사 로그','audit','🗒 감사 로그')}
{a('api_docs','API 문서','api','📖 API 문서')}
        </div>
      </div>

    </div>
  </div>"""
    return html

# Config for each template
CONFIGS = {
    "portal_news.html":         {"mgmt_active": "news"},
    "portal_contacts.html":     {"mgmt_active": "contacts"},
    "portal_audit.html":        {"mgmt_active": "audit"},
    "portal_files.html":        {"data_active": "files"},
    "portal_participants.html": {"data_active": "participants"},
    "portal_sessions.html":     {"data_active": "sessions"},
    "portal_merge.html":        {"data_active": "merge"},
    "portal_settings.html":     {"mgmt_active": "settings"},
    "portal_wardy.html":        {"app_active": True},
}

# Pattern for existing simplified nav blocks to replace
SIMPLE_NAV_PATTERNS = [
    # portal-nav div containing only simple pnav-btn links (no dropdowns)
    r'  <!-- Nav -->\s*\n  <div class="portal-nav">.*?</div>\s*\n  </div>',
    r'  <div class="portal-nav">\s*\n    <div class="container portal-nav-inner">(?:(?!pnav-drop).)*?</div>\s*\n  </div>',
]

def fix_template(fname, **kwargs):
    fpath = os.path.join(TEMPLATES_DIR, fname)
    if not os.path.exists(fpath):
        print(f"  SKIP (not found): {fname}")
        return

    with open(fpath, encoding="utf-8") as f:
        content = f.read()

    original = content

    # 1. Replace old nav CSS in style block
    old_nav_css_patterns = [
        r"\.portal-nav\{[^}]+\}\s*\n\.portal-nav-inner\{[^}]+\}\s*\n\.pnav-btn\{[^}]+\}\s*\n\.pnav-btn:hover\{[^}]+\}\s*\n\.pnav-btn\.active\{[^}]+\}",
        r"\.portal-nav\{[^}]+\}\s*\.portal-nav-inner\{[^}]+\}\s*\.pnav-btn\{[^}]+\}\s*\.pnav-btn:hover\{[^}]+\}\s*\.pnav-btn\.active\{[^}]+\}",
    ]
    for pat in old_nav_css_patterns:
        content = re.sub(pat, NAV_CSS, content, flags=re.DOTALL)

    # If NAV_CSS wasn't inserted (no match), inject before </style>
    if NAV_CSS not in content:
        content = content.replace("</style>", NAV_CSS + "\n</style>", 1)

    # 2. Replace simplified nav HTML
    new_nav = make_nav(**kwargs)

    # Try to match and replace the simple nav block
    # Pattern: <!-- Nav --> ... </div>\n  </div>  OR  <div class="portal-nav"> ... </div>
    nav_replaced = False
    # Match nav comment + div
    m = re.search(r'  <!-- Nav -->\s*\n  <div class="portal-nav">.*?</div>\s*\n  </div>', content, re.DOTALL)
    if m:
        content = content[:m.start()] + new_nav + content[m.end():]
        nav_replaced = True

    if not nav_replaced:
        # Match just the portal-nav div (no dropdowns inside)
        m = re.search(r'  <div class="portal-nav">\s*\n    <div class="container portal-nav-inner">(?:(?!pnav-drop)[\s\S])*?</div>\s*\n  </div>', content, re.DOTALL)
        if m:
            content = content[:m.start()] + new_nav + content[m.end():]
            nav_replaced = True

    if not nav_replaced:
        print(f"  WARN: Could not find nav block in {fname}")

    # 3. Add JS click-toggle if pnav-drop exists and not already present
    if "pnav-drop-btn" in content and NAV_JS not in content:
        if "{% block scripts %}" in content:
            content = content.replace(
                "{% block scripts %}",
                "{% block scripts %}\n<script>\n" + NAV_JS + "\n</script>"
            )
        elif "{% endblock %}" in content:
            # Add before last endblock
            last_endblock = content.rfind("{% endblock %}")
            insert = "\n{% block scripts %}\n<script>\n" + NAV_JS + "\n</script>\n{% endblock %}\n"
            content = content[:last_endblock] + insert + content[last_endblock:]

    if content != original:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Fixed: {fname}")
    else:
        print(f"  No changes: {fname}")

print("Updating portal nav in management pages...")
for fname, cfg in CONFIGS.items():
    fix_template(fname, **cfg)

print("\nDone.")
